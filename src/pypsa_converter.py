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
from typing import cast

from pypsa import Network

from src.antares_hybrid_writer import AntaresHybridStudyWriter
from src.gems_model_builder import GemsModelBuilder
from src.gems_study_writer import GemsStudyWriter
from src.models.gems_system_yml_schema import GemsComponent, GemsPortConnection
from src.pypsa_preprocessor import PyPSAPreprocessor
from src.pypsa_register import PyPSARegister
from src.utils import check_time_series_format, determine_pypsa_study_type

# PyPSA component types that carry a Kirchhoff Voltage Law obligation (n.cycle_matrix()'s
# "type" index level uses these exact names). Matches PyPSA's own passive_branch_components.
_KVL_BRANCH_TYPES: frozenset[str] = frozenset({"Line", "Transformer"})

CONVERTER_LOGGER_NAME = "pypsa_to_gems_converter"
_CONVERTER_LOG = logging.getLogger(CONVERTER_LOGGER_NAME)

# Components carrying an extendable capacity variable, keyed by their "*_extendable" column.
_EXTENDABLE_CAPACITY_FLAGS: dict[str, str] = {
    "generators": "p_nom_extendable",
    "links": "p_nom_extendable",
    "storage_units": "p_nom_extendable",
    "stores": "e_nom_extendable",
    # For consideration, currently not processed
    # "lines": "s_nom_extendable",
    # "transformers": "s_nom_extendable",
}

# pypsa_models.yml model id for each of the component types above. optim-config-full-gems.yml
# declares a model-decomposition entry for all four; GemsPy's validate_optim_config() (called
# by gems_runner.study.runner.run_study()) rejects any entry whose model isn't actually used
# by a component in the system, so full-GEMS studies must filter to only the types present.
_INVESTMENT_MODEL_IDS: dict[str, str] = {
    "generators": "pypsa_models.generator",
    "links": "pypsa_models.link",
    "storage_units": "pypsa_models.storage_unit",
    "stores": "pypsa_models.store",
}


class PyPSAStudyConverter:
    def __init__(
        self,
        pypsa_network: Network,
        study_dir: Path,
        series_file_format: str,
        solver_name: str = "highs",
        full_gems: bool = False,
    ):
        """
        Initialize processor. The network is deep-copied internally so the caller's
        object is never mutated. Note: do not pass a network that has been optimized
        (network.optimize()), as it contains non-copyable solver state (e.g. HiGHS).

        ``full_gems``: for investment studies, write a pure GEMS study runnable directly
        via GemsPy's own ``gems_runner.study.runner.run_study()`` -- no companion classic
        Antares study with an inert "virtual area" is written, and no Xpansion-launcher
        inputs (settings.ini/weights.txt) are needed. Requires a GemsPy-native solver
        ("highs", "xpress", or "gurobi") instead of the Xpansion-launcher's "coin"/"xpress".
        Has no effect on non-investment studies.

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
        self.full_gems = full_gems
        self.is_investment_study = self._has_extendable_capacity()

        if self.is_investment_study:
            # Benders path: extendable capacity, regardless of scenario count.
            if self.full_gems:
                self._validate_gemspy_solver()
            else:
                self._validate_xpansion_solver()
        elif len(self.scenario_weightings) > 1:
            # Operational multi-scenario path: GemsPy expec() is still unweighted 1/N.
            self._validate_scenario_weightings()

        # Preprocess the network
        self.pypsa_network = PyPSAPreprocessor(self.pypsa_network).network_preprocessing()
        # Register the PyPSA components and global constraints
        self.pypsa_components_data, self.pypsa_globalconstraints_data = PyPSARegister(self.pypsa_network).register()

    def _validate_xpansion_solver(self) -> None:
        """Investment studies are solved end-to-end via the antares-xpansion-launcher
        GEMS workflow, which only supports the 'coin' and 'xpress' solvers."""
        if self.solver_name.lower() not in {"coin", "xpress"}:
            raise ValueError("Investment studies support only 'coin' and 'xpress' solvers.")

    def _validate_gemspy_solver(self) -> None:
        """Full-GEMS investment studies are resolved via GemsPy's benders-decomposition
        mode, whose master/subproblem LPs are exported as MPS and solved externally by the
        same Cbc-based 'benders' binary the Xpansion-launcher path uses -- SimulationSession
        never reads OptimConfig.solver-options.name for this resolution mode, so 'coin' is
        accepted here too (in addition to 'highs'/'xpress'/'gurobi', which GemsPy's own
        frontal/sequential/parallel resolution modes do read this field for)."""
        if self.solver_name.lower() not in {"highs", "xpress", "gurobi", "coin"}:
            raise ValueError(
                "Full-GEMS investment studies support only 'highs', 'xpress', 'gurobi', or 'coin' solvers."
            )

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

    def _has_extendable_capacity(self) -> bool:
        """
        Whether the network has at least one component with a free (extendable) capacity variable.

        This is what actually makes a study an investment problem, independently of scenario count:
        p_nom/e_nom is a decision variable only when *_extendable=True. Non-extendable components
        have their bounds fixed to the same value by the preprocessor
        (see PyPSAPreprocessor._fix_capacity_non_extendable_attribute), so they never introduce a
        master variable.
        """
        for component_type, extendable_col in _EXTENDABLE_CAPACITY_FLAGS.items():
            df = getattr(self.pypsa_network, component_type)
            if len(df) > 0 and bool(df[extendable_col].any()):
                return True
        return False

    def _investment_model_ids_present(self) -> set[str]:
        """pypsa_models.* ids actually used by a component in this network.

        Every one of these model types declares p_nom/e_nom as a master-and-subproblems
        decomposition variable regardless of whether any instance is extendable (see
        AntaresHybridStudyWriter's module docstring), so presence -- not extendability --
        is what determines whether optim-config.yml may reference the model.
        """
        return {
            model_id
            for component_type, model_id in _INVESTMENT_MODEL_IDS.items()
            if len(getattr(self.pypsa_network, component_type)) > 0
        }

    def _write_execution_outputs(self, gems_study_writer: GemsStudyWriter) -> None:
        """
        Extra outputs that make the converted study runnable end-to-end.

        Branching is on *extendable capacity*, not on scenario count:

        - Investment, full_gems=True: write only a GemsPy-native optim-config.yml (with
          `block-length`/`scenario-scope`). No legacy virtual-area hybrid study and no
          Xpansion-launcher inputs -- GemsPy's own runner (PR #298) does the
          per-(scenario, week-block) Benders split itself.
        - Investment, full_gems=False (any number of scenarios): write the legacy
          optim-config.yml and Xpansion launcher inputs (settings.ini / yearly-weights).
          Also write a companion hybrid Antares study when the horizon is a multiple of
          168 hours so antares-problem-generator can emit Benders master/slave MPS (one
          subproblem per scenario / MC year).
        - Operational + multi-scenario: write the same hybrid study so antares-solver can
          run every Monte-Carlo year. Same 168-hour restriction (Antares Economy truncates
          incomplete weeks; see StudyRuntimeInfos::initializeRangeLimits).
        - Operational + single scenario: nothing extra; antares-modeler on systems/ is enough.
        """
        if self.is_investment_study:
            n_timesteps = len(self.pypsa_network.snapshots)
            if self.full_gems:
                gems_study_writer.write_optim_config_yml(
                    full_gems=True,
                    n_scenarios=len(self.scenario_weightings),
                    last_time_step=n_timesteps - 1,
                    solver_name=self.solver_name,
                    model_ids_present=self._investment_model_ids_present(),
                )
                self.logger.info(
                    "Full-GEMS investment study written to %s (run: gems_runner.study.runner."
                    "run_study(Path(%r) / 'systems'))",
                    self.study_dir / "systems",
                    str(self.study_dir),
                )
                return

            # optim-config.yml's model-decomposition is what lets the
            # antares-xpansion-launcher GEMS workflow (antares-problem-generator + benders)
            # run the master/subproblem Benders split end-to-end — including the 1-scenario case.
            gems_study_writer.write_optim_config_yml()
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

        if len(self.scenario_weightings) <= 1:
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
        )

    def _build_kvl_cycle_components(self) -> tuple[list[GemsComponent], list[GemsPortConnection]]:
        """Kirchhoff Voltage Law, cycle-flow formulation: one kvl_cycle component per
        independent loop in the Line/Transformer graph.

        Uses PyPSA's own n.cycle_matrix() to find the loops -- the same method PyPSA's own
        optimizer calls internally (see define_kirchhoff_voltage_constraints in
        pypsa/optimization/constraints.py) -- rather than reimplementing graph cycle
        detection. Each participating branch connects one of its two signed ports
        (pos_cycle_port for +x*p0, neg_cycle_port for -x*p0) to that cycle's component,
        matching the sign cycle_matrix() reports for that branch in that specific loop.

        Called after preprocessing, so branch names here already match the renamed
        "line_<name>"/"transformer_<name>" ids used elsewhere in the converted study.
        """
        components: list[GemsComponent] = []
        connections: list[GemsPortConnection] = []

        cycles = self.pypsa_network.cycle_matrix(apply_weights=False)
        if cycles.empty:
            return components, connections

        for cycle_idx in cycles.columns:
            cycle_id = f"kvl_cycle_{cycle_idx}"
            components.append(GemsComponent(id=cycle_id, model=f"{self.pypsalib_id}.kvl_cycle"))

            for branch_key, sign in cycles[cycle_idx].items():
                branch_type, branch_name = cast("tuple[str, str]", branch_key)
                if sign == 0 or branch_type not in _KVL_BRANCH_TYPES:
                    continue
                port = "pos_cycle_port" if sign > 0 else "neg_cycle_port"
                connections.append(
                    GemsPortConnection(
                        component1=str(branch_name),
                        port1=port,
                        component2=cycle_id,
                        port2="branch_port",
                    )
                )

        return components, connections

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

        kvl_components, kvl_connections = self._build_kvl_cycle_components()
        list_components.extend(kvl_components)
        list_connections.extend(kvl_connections)

        system_id = self.system_name if self.system_name not in {"", None} else "pypsa_to_gems_converter"
        gems_study_writer.write_gems_system_yml(list_components, list_connections, system_id, self.pypsalib_id)
        gems_study_writer.write_antares_modeler_parameters_yml(len(self.pypsa_network.snapshots) - 1, self.solver_name)

        self._write_execution_outputs(gems_study_writer)
        self.logger.info("Study conversion completed!")
