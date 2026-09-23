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
import math
import shutil
from pathlib import Path
from typing import Any

import polars as pl
import yaml

from src.models.gems_system_yml_schema import GemsComponent, GemsPortConnection, GemsSystem
from src.models.modeler_parameter_yml_schema import AntaresModelerParameters
from src.models.pypsa_model_schema import PyPSAComponentData

_COLUMN_SEP = "__"


class GemsStudyWriter:
    def __init__(self, study_dir: Path, series_file_format: str):
        self.study_dir = study_dir
        self.series_dir = study_dir / "systems" / "input" / "data-series"
        self.series_file_format = series_file_format
        self.separator = "," if series_file_format == ".csv" else "\t"

    def copy_library_yml(self) -> None:
        Path(self.study_dir / "systems" / "input" / "model-libraries").mkdir(parents=True, exist_ok=True)
        destination_file = Path(self.study_dir / "systems" / "input" / "model-libraries" / "pypsa_models.yml")
        destination_file.touch()

        project_root = Path(__file__).parent.parent
        source_file = project_root / "resources" / "pypsa_models" / "pypsa_models.yml"
        shutil.copy(source_file, destination_file)

    def write_gems_system_yml(
        self,
        list_components: list[GemsComponent],
        list_connections: list[GemsPortConnection],
        system_id: str,
        pypsalib_id: str,
    ) -> None:
        GemsSystem(
            id=system_id,
            components=list_components,
            connections=list_connections,
            model_libraries=pypsalib_id,
            area_connections=None,
        ).to_yml(self.study_dir / "systems" / "input" / "system.yml")

    def write_antares_modeler_parameters_yml(self, last_time_step: int, solver_name: str) -> None:
        AntaresModelerParameters(
            solver=solver_name,
            solver_logs=False,
            solver_parameters="THREADS 1",
            no_output=False,
            first_time_step=0,
            last_time_step=last_time_step,
        ).to_yml(self.study_dir / "systems" / "parameters.yml")

    def _write_and_register_timeseries(
        self,
        time_dependent_data: dict[str, pl.DataFrame],
        constant_data: pl.DataFrame,
        pypsa_components_data: PyPSAComponentData,
        system_name: str,
    ) -> tuple[dict[tuple[str, str], str | list[str | bool]], dict[tuple[str, str], str | float]]:
        # take all time-dependent data
        time_dep_keys = set(pypsa_components_data.time_dependent_data.keys())
        # take all parameters that are time-dependent
        time_dependent_params = set(pypsa_components_data.pypsa_params_to_gems_params).intersection(time_dep_keys)
        # treat time-dependent parameters
        comp_param_to_timeseries_file_name, comp_param_to_skip_in_static_treatment = (
            self._treat_time_dependent_parameters(
                time_dependent_params,
                time_dependent_data,
                system_name,
            )
        )

        # Treat static parameters
        static_params = set(pypsa_components_data.pypsa_params_to_gems_params).intersection(set(constant_data.columns))
        comp_param_to_static_file_name = self._treat_static_parameters(
            static_params,
            constant_data,
            system_name,
            comp_param_to_skip_in_static_treatment,
        )
        return comp_param_to_timeseries_file_name, comp_param_to_static_file_name

    def _treat_time_dependent_parameters(
        self,
        time_dependent_params: set[str],
        time_dependent_data: dict[str, pl.DataFrame],
        system_name: str,
    ) -> tuple[dict[tuple[str, str], str | list[str | bool]], set[tuple[str, str]]]:
        """Process time-dependent parameters: write series files and build mappings.
        Returns (comp_param_to_timeseries_file_name,  comp_param_to_skip_in_static_treatment).
        The latter is used by _treat_static_parameters to avoid duplicating scenarized data."""
        comp_param_to_timeseries_file_name: dict[tuple[str, str], str | list[str | bool]] = {}
        comp_param_to_skip_in_static_treatment: set[tuple[str, str]] = set()

        if time_dependent_params:
            self.series_dir.mkdir(parents=True, exist_ok=True)

        for param in time_dependent_params:
            param_df = time_dependent_data[param]
            # Columns are time_step + "scenario__component"; get unique component names (order preserved)
            data_cols = [c for c in param_df.columns if c != "time_step"]
            component_names = list(dict.fromkeys(c.split(_COLUMN_SEP, 1)[-1] for c in data_cols))

            for component in component_names:
                comp_cols = [c for c in data_cols if c.split(_COLUMN_SEP, 1)[-1] == component]
                component_data = param_df.select(comp_cols)
                multiple_scenario_indicator = len(comp_cols) > 1

                comp_param_to_skip_in_static_treatment.add((component, param))

                timeseries_name = f"{system_name}_{component}_{param}"

                comp_param_to_timeseries_file_name[(component, param)] = [
                    timeseries_name,
                    multiple_scenario_indicator,
                ]

                self._write_time_series_file(
                    component_data,
                    self.series_dir / Path(f"{timeseries_name}{self.series_file_format}"),
                    self.separator,
                )

        return comp_param_to_timeseries_file_name, comp_param_to_skip_in_static_treatment

    def _treat_static_parameters(
        self,
        static_params: set[str],
        constant_data: pl.DataFrame,
        system_name: str,
        comp_param_to_skip_in_static_treatment: set[tuple[str, str]],
    ) -> dict[tuple[str, str], str | float]:
        comp_param_to_static_file_name: dict[tuple[str, str], str | float] = {}

        for param in static_params:
            static_component_names = constant_data["component"].unique(maintain_order=True).to_list()
            for component in static_component_names:
                if (component, param) not in comp_param_to_skip_in_static_treatment:
                    comp_df = constant_data.filter(pl.col("component") == component)
                    component_values = comp_df[param].to_list()

                    # only if we have multiple different values for the same parameter, we need to create a time series file for static parameters
                    # in that case we will have static scenarized parameter
                    # if we have only one value, we can use the value directly (it will be used over all scenarios)
                    if len(set(component_values)) > 1:
                        scenario_names = comp_df["scenario"].to_list()
                        scenario_data = pl.DataFrame({str(s): [v] for s, v in zip(scenario_names, component_values)})
                        timeseries_name = f"{system_name}_{component}_{param}"
                        comp_param_to_static_file_name[(component, param)] = timeseries_name
                        self._write_time_series_file(
                            scenario_data,
                            self.series_dir / Path(f"{timeseries_name}{self.series_file_format}"),
                            self.separator,
                        )
                    else:
                        component_value = list(set(component_values))[0]
                        comp_param_to_static_file_name[(component, param)] = self.sanitize_component_value(
                            component_value
                        )
        return comp_param_to_static_file_name

    def sanitize_component_value(self, component_value: float) -> float:
        if component_value is None or (isinstance(component_value, float) and math.isnan(component_value)):
            component_value = 0.0
        elif component_value == float("inf"):
            component_value = 1e20
        elif component_value == float("-inf"):
            component_value = -1e20
        return component_value

    def _write_time_series_file(self, data: pl.DataFrame, series_dir: Path, separator: str) -> None:
        data.write_csv(
            series_dir,
            include_header=False,
            separator=separator,
        )

    def write_optim_config_yml(
        self,
        full_gems: bool = False,
        n_scenarios: int = 1,
        last_time_step: int = 0,
        solver_name: str = "highs",
        model_ids_present: set[str] | None = None,
    ) -> None:
        """Write optim-config.yml.

        - ``full_gems=False`` (default): copy the legacy flat-schema template as-is. It is
          consumed by the antares-solver hybrid study loader (see AntaresHybridStudyWriter),
          which requires its own separate static copy and does not read this file.
        - ``full_gems=True``: write GemsPy's native OptimConfig schema (nested `resolution`
          block, `scenario-scope`/`time-scope`/`solver-options`), consumed directly by
          ``gems_runner.study.runner.run_study()`` -- no companion hybrid study needed.
          ``block-length: 168`` (one Antares week) splits each Monte-Carlo year into
          per-week Benders subproblems; ``scenario-scope`` selects the MC years.
          ``model_ids_present`` filters the static template's ``models`` list down to the
          decomposition entries actually used by this study -- GemsPy's
          ``validate_optim_config()`` (called by ``run_study()``) raises if ``models``
          references a model id absent from the converted system. ``None`` keeps every
          entry in the template (used by direct/unit-level callers of this method).
        """
        Path(self.study_dir / "systems" / "input" / "model-libraries").mkdir(parents=True, exist_ok=True)
        destination_file = Path(self.study_dir / "systems" / "input" / "optim-config.yml")

        project_root = Path(__file__).parent.parent
        if not full_gems:
            destination_file.touch()
            shutil.copy(project_root / "resources" / "optim-config.yml", destination_file)
            return

        with (project_root / "resources" / "optim-config-full-gems.yml").open(encoding="utf-8") as f:
            static_config: dict[str, Any] = yaml.safe_load(f)

        if model_ids_present is not None:
            static_config["models"] = [
                model for model in static_config["models"] if model["id"] in model_ids_present
            ]

        scenario_scope_include: list[int | str] = [0] if n_scenarios <= 1 else [f"0-{n_scenarios - 1}"]
        config: dict[str, Any] = {
            "time-scope": {"first-time-step": 0, "last-time-step": last_time_step},
            "scenario-scope": {"include": scenario_scope_include},
            "solver-options": {"name": solver_name.lower()},
            **static_config,
        }

        with destination_file.open("w", encoding="utf-8") as f:
            yaml.dump(config, f, allow_unicode=True, sort_keys=False)

    def prepare_xpansion_runnable_study(self, solver_name: str, scenario_weights: dict[str, float]) -> None:
        """
        Write the Antares-Xpansion inputs the launcher GEMS workflow needs on top of the GEMS study.
        - ``user/expansion/settings.ini`` configures the algorithm, including scenario/year weights.

        Each PyPSA scenario is mapped to one Monte-Carlo year by the Antares-Xpansion GEMS
        workflow (including the single-scenario case). We export the PyPSA scenario
        probabilities as Xpansion ``yearly-weights``.
        """
        solver = solver_name.lower()
        if solver not in {"coin", "xpress"}:
            raise ValueError("Investment studies support only 'coin' and 'xpress' solvers.")
        if not scenario_weights:
            raise ValueError("scenario_weights must be non-empty for investment studies.")

        study_root = self.study_dir / "systems"
        settings_path = study_root / "user" / "expansion" / "settings.ini"
        weights_dir = study_root / "user" / "expansion" / "weights"
        weights_path = weights_dir / "weights.txt"

        # Antares-Xpansion expects one weight per Monte-Carlo year (scenario).
        # We keep the order given by the scenario_weights mapping (which comes from PyPSA's scenario_weightings).
        weights_content = "\n".join(str(float(w)) for w in scenario_weights.values()) + "\n"
        weights_dir.mkdir(parents=True, exist_ok=True)
        weights_path.write_text(weights_content, encoding="utf-8")

        xpansion_solver = "Cbc" if solver == "coin" else "Xpress"
        settings_content = "\n".join(
            [
                f"solver = {xpansion_solver}",
                "yearly-weights = weights.txt",
                # Tight gaps so Benders' overall_cost is comparable with PyPSA (pytest.approx).
                "optimality_gap = 0",
                "relative_gap = 1e-6",
                "",
            ]
        )
        self._write_text_file(settings_path, settings_content)

    def _write_text_file(self, path: Path, content: str) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")
