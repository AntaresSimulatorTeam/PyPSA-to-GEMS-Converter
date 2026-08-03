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
from pathlib import Path

from pypsa import Network

from src.antares_hybrid_writer import AntaresHybridStudyWriter
from src.gems_model_builder import GemsModelBuilder
from src.gems_study_writer import GemsStudyWriter
from src.pypsa_preprocessor import PyPSAPreprocessor
from src.pypsa_register import PyPSARegister
from src.utils import check_time_series_format, determine_pypsa_study_type

CONVERTER_LOGGER_NAME = "pypsa_to_gems_converter"
_CONVERTER_LOG = logging.getLogger(CONVERTER_LOGGER_NAME)

# Components carrying an extendable capacity variable, keyed by their "*_extendable" column.
_EXTENDABLE_CAPACITY_COMPONENTS: dict[str, str] = {
    "generators": "p_nom_extendable",
    "links": "p_nom_extendable",
    "storage_units": "p_nom_extendable",
    "stores": "e_nom_extendable",
    # For consideration: do we want to support this too? Lines/transformers have their
    # own extendable-capacity attribute (s_nom_extendable). Left out for now, so a
    # study with only extendable lines/transformers (no extendable generator/link/
    # storage_unit/store) is currently misclassified as non-investment here and would
    # get the antares-solver hybrid trick applied (see _has_extendable_capacity below).
    # "lines": "s_nom_extendable",
    # "transformers": "s_nom_extendable",
}


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
        self._validate_scenario_weightings()
        self.solver_name = solver_name

        # Preprocess the network
        self.pypsa_network = PyPSAPreprocessor(self.pypsa_network).network_preprocessing()
        # Register the PyPSA components and global constraints
        self.pypsa_components_data, self.pypsa_globalconstraints_data = PyPSARegister(self.pypsa_network).register()

    def _validate_scenario_weightings(self) -> None:
        """
        Multi-scenario, non-investment studies currently require every scenario to carry the
        SAME weight.

        GemsPy's own ``expec()`` operator (used to combine each scenario's operational
        objective contribution) currently computes a plain, unweighted arithmetic mean
        across scenarios -- it does not consume PyPSA's scenario_weightings at all. With
        equal weights the unweighted and true weighted average coincide, so results are
        correct; with unequal weights they silently diverge (see
        tests/e2e/test_hybrid_study_comparison.py::
        test_hybrid_two_scenarios_unequal_weights_matches_pypsa for a worked example: pypsa's
        true weighted objective differs from GemsPy's for 0.1/0.9 weights, but not for
        0.5/0.5). Rather than silently producing a GEMS study whose GemsPy/antares-modeler
        results don't match PyPSA's, we fail fast at conversion time until GemsPy's
        ``expec()`` supports per-scenario weights.

        Investment studies (run through ``antares-xpansion-launcher``, see
        ``GemsStudyWriter.prepare_xpansion_runnable_study``) currently have the SAME restriction:
        the launcher's Benders decomposition weights every Monte-Carlo year uniformly by default
        (confirmed empirically -- see ``tests/e2e/test_xpansion_study_comparison.py``), and this
        converter does not yet emit a custom per-scenario Xpansion ``yearly-weights`` file (its
        exact expected file format -- one ``<mps path> <weight>`` line per subproblem plus a
        trailing ``WEIGHT_SUM`` line, normally produced by an internal weight-merging step -- is
        not stably reproducible from here). So equal weights are required across the board until
        that's wired up.
        """
        weights = list(self.scenario_weightings.values())
        if len(weights) <= 1:
            return
        reference = weights[0]
        if not all(math.isclose(w, reference, rel_tol=1e-9, abs_tol=1e-12) for w in weights):
            raise ValueError(
                "Multi-scenario studies currently require every scenario to have the same "
                f"weight, but got unequal weights: {self.scenario_weightings!r}. Neither GemsPy's "
                "expec() (non-investment path) nor antares-xpansion-launcher's default Benders "
                "decomposition (investment path) consume PyPSA's true per-scenario weights yet, "
                "so unequal weights would silently produce results that don't match PyPSA's "
                "true (probability-weighted) objective. Use equal weights for every scenario."
            )

    def _has_extendable_capacity(self) -> bool:
        """
        Whether the network has at least one component with a free (extendable) capacity variable.

        This is what actually makes a study an investment problem, independently of scenario count:
        p_nom/e_nom is a decision variable only when *_extendable=True. Non-extendable components
        have their bounds fixed to the same value by the preprocessor
        (see PyPSAPreprocessor._fix_capacity_non_extendable_attribute), so they never introduce a
        master variable.
        """
        for component_type, extendable_col in _EXTENDABLE_CAPACITY_COMPONENTS.items():
            df = getattr(self.pypsa_network, component_type)
            if len(df) > 0 and bool(df[extendable_col].any()):
                return True
        return False

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
        # One scenario -> deterministic study, nothing more to write.
        if len(self.scenario_weightings.keys()) > 1:
            is_investment = self._has_extendable_capacity()
            if is_investment:
                # Investment study: optim-config.yml's model-decomposition is what lets
                # antares-modeler/GemsPy run the master/subproblem Benders split, but
                # antares-modeler invoked directly only ever solves scenario 0 (same
                # limitation as the non-investment case below).
                if self.solver_name.lower() not in {"coin", "xpress"}:
                    raise ValueError(
                        "Multi-scenario investment studies are run through antares-xpansion-launcher, "
                        f"which only supports the 'coin' and 'xpress' solvers, got {self.solver_name!r}."
                    )
                gems_study_writer.write_optim_config_yml()

            # Multi-scenario studies (investment or not) are directly runnable end-to-end
            # via the real Antares engine only through the companion classic Antares study
            # wired by the trick validated in tests/e2e/test_hybrid_study_comparison.py /
            # tests/e2e/test_xpansion_study_comparison.py: a bare classic study whose only
            # area is inert, with the GEMS system.yml grafted into its own input/ directory,
            # and nb_years set to the scenario count.
            antares_hybrid_dir = AntaresHybridStudyWriter(self.study_dir).write(
                gems_systems_dir=self.study_dir / "systems",
                pypsa_network=self.pypsa_network,
                n_scenarios=len(self.scenario_weightings),
            )
            if is_investment:
                # `antares-xpansion-launcher`'s GEMS driver understands Monte-Carlo years and
                # runs Benders decomposition across every scenario (unlike antares-modeler
                # invoked directly, which only ever solves scenario 0); it only needs a
                # `user/expansion/settings.ini` on top of the classic hybrid study (see
                # GemsStudyWriter.prepare_xpansion_runnable_study).
                gems_study_writer.prepare_xpansion_runnable_study(
                    solver_name=self.solver_name, target_dir=antares_hybrid_dir
                )
                self.logger.info(
                    "Antares-Xpansion-runnable study written to %s "
                    "(run: antares-xpansion-launcher -i %s)",
                    antares_hybrid_dir,
                    antares_hybrid_dir,
                )
            else:
                self.logger.info(
                    "Antares-runnable hybrid study written to %s (run: antares-solver -i %s)",
                    antares_hybrid_dir,
                    antares_hybrid_dir,
                )
        self.logger.info("Study conversion completed!")
