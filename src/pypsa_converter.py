# Copyright (c) 2026, RTE (https://www.rte-france.com)
#
# See AUTHORS.txt
#
# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at http://mozilla.org/MPL/2.0/.
#
# SPDX-License-Identifier: MPL-2.0
#
# This file is part of the Antares project.
import copy
import logging
import math
import shutil
from pathlib import Path

from pypsa import Network

from src.antares_hybrid_writer import AntaresHybridStudyWriter
from src.gems_model_builder import GemsModelBuilder
from src.gems_study_writer import GemsStudyWriter
from src.optim_config_builder import build_hybrid_optim_config, build_optim_config
from src.pypsa_preprocessor import PyPSAPreprocessor
from src.pypsa_register import PyPSARegister
from src.study_type import StudyType, classify_study
from src.utils import check_time_series_format, determine_pypsa_study_type

CONVERTER_LOGGER_NAME = "pypsa_to_gems_converter"
_CONVERTER_LOG = logging.getLogger(CONVERTER_LOGGER_NAME)


class PyPSAStudyConverter:
    def __init__(
        self,
        pypsa_network: Network,
        study_dir: Path,
        series_file_format: str,
        solver_name: str = "highs",
    ):
        """
        Initialize processor. The network is deep-copied internally so the caller's
        object is never mutated. Note: do not pass a network that has been optimized
        (network.optimize()), as it contains non-copyable solver state (e.g. HiGHS).

        Logging uses the logger named ``pypsa_to_gems_converter``. To see INFO/DEBUG
        messages, configure the standard library (e.g. ``logging.basicConfig``) or attach
        handlers to that logger or the root logger.
        """
        self.logger = _CONVERTER_LOG
        self.study_dir = study_dir
        self.pypsa_network = copy.deepcopy(pypsa_network)
        self.pypsalib_id = "pypsa_models"
        self.system_name = pypsa_network.name
        self.series_file_format = check_time_series_format(series_file_format)
        self.pypsa_network, self.scenario_weightings = determine_pypsa_study_type(self.pypsa_network)
        self.solver_name = solver_name
        self.study_type = classify_study(self.pypsa_network, len(self.scenario_weightings))

        if self.study_type is StudyType.INVESTMENT_MULTI_SCENARIO:
            # Benders / Xpansion path.
            self._validate_xpansion_solver()
        elif self.study_type is StudyType.OPERATIONAL_MULTI_SCENARIO:
            # Operational multi-scenario path: GemsPy expec() is still unweighted 1/N.
            self._validate_scenario_weightings()

        # Preprocess the network
        self.pypsa_network = PyPSAPreprocessor(self.pypsa_network).network_preprocessing()
        # Register the PyPSA components and global constraints
        self.pypsa_components_data, self.pypsa_globalconstraints_data = PyPSARegister(self.pypsa_network).register()

    def _validate_xpansion_solver(self) -> None:
        """Multi-scenario investment studies are solved end-to-end via the antares-xpansion-launcher
        GEMS workflow, which only supports the 'coin' and 'xpress' solvers."""
        if self.solver_name.lower() not in {"coin", "xpress"}:
            raise ValueError("Multi-scenario investment studies support only 'coin' and 'xpress' solvers.")

    def _validate_scenario_weightings(self) -> None:
        """
        Multi-scenario, non-investment studies currently require every scenario to carry the SAME weight.
        Because of GEMSPy behavior, 1/N where N is the number of scenarios.
        """
        weights = list(self.scenario_weightings.values())
        if len(weights) <= 1:
            return
        reference = weights[0]
        if not all(math.isclose(w, reference, rel_tol=1e-9, abs_tol=1e-12) for w in weights):
            raise ValueError(
                "Multi-scenario, non-investment studies currently require every scenario to have the same "
                f"weight, but got unequal weights: {self.scenario_weightings!r}. GemsPy's "
                "expec() operator computes an unweighted average across scenarios, so "
                "unequal weights would silently produce GemsPy/antares-modeler results that "
                "don't match PyPSA's true (probability-weighted) objective. Use equal "
                "weights for every scenario until GemsPy's expec() supports per-scenario "
                "weights."
            )

    def _write_execution_outputs(self, gems_study_writer: GemsStudyWriter, used_model_ids: set[str]) -> None:
        """
        Extra outputs that make the converted study runnable end-to-end.

        Branching is on the study type:

        - Single scenario (operational or investment): nothing extra; antares-modeler on systems/
          solves the study with sequential-subproblems.
        - Investment + multi-scenario: write Xpansion launcher inputs (settings.ini / yearly-weights).
          Also write a companion hybrid Antares study when the horizon is a multiple of 168 hours so
          antares-problem-generator can emit Benders master/slave MPS (one subproblem per
          scenario / MC year).
        - Operational + multi-scenario: write the same hybrid study so antares-solver can
          run every Monte-Carlo year. Same 168-hour restriction (Antares Economy truncates
          incomplete weeks; see StudyRuntimeInfos::initializeRangeLimits).

        optim-config.yml (required by antares-modeler >= 10.2) is built by src/optim_config_builder.py.
        """
        gems_study_writer.write_optim_config_yml(build_optim_config(self.study_type, self.pypsalib_id, used_model_ids))
        if not self.study_type.is_multi_scenario:
            return

        if self.study_type.is_investment:
            # optim-config.yml's model-decomposition is what lets the
            # antares-xpansion-launcher GEMS workflow (antares-problem-generator + benders)
            # run the master/subproblem Benders split end-to-end.
            gems_study_writer.prepare_xpansion_runnable_study(
                solver_name=self.solver_name, scenario_weights=self.scenario_weightings
            )
            antares_hybrid_dir = self._write_antares_hybrid_study()
            if antares_hybrid_dir is None:
                return
            expansion_src = self.study_dir / "systems" / "user" / "expansion"
            if expansion_src.exists():
                shutil.copytree(expansion_src, antares_hybrid_dir / "user" / "expansion", dirs_exist_ok=True)
            self.logger.info(
                "Xpansion-runnable hybrid study written to %s (run: antares-xpansion-launcher -i %s)",
                antares_hybrid_dir,
                antares_hybrid_dir,
            )
            return

        antares_hybrid_dir = self._write_antares_hybrid_study()
        if antares_hybrid_dir is None:
            return
        self.logger.info(
            "Antares-runnable hybrid study written to %s (run: antares-solver -i %s)",
            antares_hybrid_dir,
            antares_hybrid_dir,
        )

    def _write_antares_hybrid_study(self) -> Path | None:
        """Write the companion classic Antares study used for Benders / MC years.

        Returns None when the horizon is not a whole number of Antares weeks.
        """
        n_timesteps = len(self.pypsa_network.snapshots)
        if n_timesteps % 168 != 0:
            self.logger.warning(
                "Horizon has %s timesteps, not a multiple of 168 (full weeks). "
                "Skipping the Antares hybrid study; Antares Economy would drop the incomplete trailing week. "
                "GEMS systems/ is still written.",
                n_timesteps,
            )
            return None

        return AntaresHybridStudyWriter(self.study_dir, study_name=self.pypsa_network.name).write(
            gems_systems_dir=self.study_dir / "systems",
            n_timesteps=n_timesteps,
            n_scenarios=len(self.scenario_weightings),
            optim_config=build_hybrid_optim_config(self.pypsalib_id),
        )

    def to_gems_study(self) -> None:
        """Main function, to export PyPSA as Gems study"""

        self.logger.info("Study conversion started")
        list_components, list_connections = [], []

        gems_study_writer = GemsStudyWriter(self.study_dir, self.series_file_format)
        self.logger.info("Copying library yml file to study directory")
        gems_study_writer.copy_library_yml()

        gems_model_builder = GemsModelBuilder(self.pypsalib_id)

        for pypsa_components_data in self.pypsa_components_data.values():
            # We test whether the keys of the conversion dictionary are allowed in the PyPSA model : all authorized parameters are columns in the constant data frame (even though they are specified as time-varying values in the time-varying data frame)
            pypsa_components_data.check_params_consistency()

            # Save time series and memorize the time-dependent parameters, also save static scenarized parameters
            comp_param_to_timeseries_name, comp_param_to_static_name = gems_study_writer._write_and_register_timeseries(
                pypsa_components_data.time_dependent_data,
                pypsa_components_data.constant_data,
                pypsa_components_data,
                self.system_name,
            )

            components, connections = gems_model_builder.convert_pypsa_components_of_given_model(
                pypsa_components_data, comp_param_to_timeseries_name, comp_param_to_static_name
            )
            list_components.extend(components)
            list_connections.extend(connections)

        for pypsa_global_constraint_data in self.pypsa_globalconstraints_data.values():
            (
                components,
                connections,
            ) = gems_model_builder._convert_pypsa_globalconstraint(pypsa_global_constraint_data)
            list_components.extend(components)
            list_connections.extend(connections)

        system_id = self.system_name if self.system_name not in {"", None} else "pypsa_to_gems_converter"
        gems_study_writer.write_gems_system_yml(list_components, list_connections, system_id, self.pypsalib_id)
        gems_study_writer.write_antares_modeler_parameters_yml(len(self.pypsa_network.snapshots) - 1, self.solver_name)

        self._write_execution_outputs(gems_study_writer, {component.model for component in list_components})
        self.logger.info("Study conversion completed!")
