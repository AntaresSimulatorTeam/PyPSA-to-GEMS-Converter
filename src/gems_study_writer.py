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
import shutil
from pathlib import Path

import pandas as pd

from src.models.gems_system_yml_schema import GemsComponent, GemsPortConnection, GemsSystem
from src.models.modeler_parameter_yml_schema import ModelerParameters
from src.models.pypsa_model_schema import PyPSAComponentData


class GemsStudyWriter:
    def __init__(self, study_dir: Path):
        self.study_dir = study_dir

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
            nodes=[],
            components=list_components,
            connections=list_connections,
            model_libraries=pypsalib_id,
            area_connections=None,
        ).to_yml(self.study_dir / "systems" / "input" / "system.yml")

    def write_modeler_parameters_yml(self, last_time_step: int, solver_name: str) -> None:
        ModelerParameters(
            solver=solver_name,
            solver_logs=False,
            solver_parameters="THREADS 1",
            no_output=False,
            first_time_step=0,
            last_time_step=last_time_step,
        ).to_yml(self.study_dir / "systems" / "parameters.yml")


    def _write_and_register_timeseries(
        self,
        time_dependent_data: dict[str, pd.DataFrame],
        constant_data: pd.DataFrame,
        pypsa_components_data: PyPSAComponentData,
        system_name: str,
        series_file_format: str,
    ) -> tuple[dict[tuple[str, str], str | list[str | bool]], dict[tuple[str, str], str | float]]:
        time_dep_keys = set(pypsa_components_data.time_dependent_data.keys())
        scenarized_time_dependent_params = set(pypsa_components_data.pypsa_params_to_gems_params).intersection(
            time_dep_keys
        )

        comp_param_to_scenario_dependent_timeseries_name: dict[tuple[str, str], str | list[str | bool]] = {}
        comp_param_static_scenarized_indicator: set[tuple[str, str]] = set()

        series_dir = self.study_dir / "systems" / "input" / "data-series"

        if scenarized_time_dependent_params:
            series_dir.mkdir(parents=True, exist_ok=True)

        for param in scenarized_time_dependent_params:
            param_df = time_dependent_data[param]
            component_names = param_df.columns.get_level_values(1).unique()
            for component in component_names:
                component_data = param_df.loc[:, (slice(None), component)]
                multiple_scenario_indicator = len(component_data.columns) > 1

                comp_param_static_scenarized_indicator.add((component, param))

                timeseries_name = f"{system_name}_{component}_{param}"

                comp_param_to_scenario_dependent_timeseries_name[(component, param)] = [
                    timeseries_name,
                    multiple_scenario_indicator,
                ]

                separator = "," if series_file_format == ".csv" else "\t"

                component_data.to_csv(
                    series_dir / Path(f"{timeseries_name}{series_file_format}"),
                    index=False,
                    header=False,
                    sep=separator,
                )

        scenarized_static_params = set(pypsa_components_data.pypsa_params_to_gems_params).intersection(
            set(constant_data.keys())
        )
        comp_param_to_scenario_dependent_static_name: dict[tuple[str, str], str | float] = {}
        index_is_multi = isinstance(constant_data.index, pd.MultiIndex) and constant_data.index.nlevels >= 2

        if "co2_emissions" in constant_data.columns:
            print("[GemsStudyWriter] constant_data has co2_emissions:", constant_data["co2_emissions"].tolist())
        else:
            print("[GemsStudyWriter] constant_data has no co2_emissions column. Columns:", list(constant_data.columns))

        for param in scenarized_static_params:
            param_series = constant_data[param]
            if param == "co2_emissions":
                print("[GemsStudyWriter] param=co2_emissions param_series:", param_series.tolist(), "index:", param_series.index.tolist())
            if index_is_multi:
                component_names = param_series.index.get_level_values(1).unique()
            else:
                component_names = param_series.index.unique()

            for component in component_names:
                if (component, param) not in comp_param_static_scenarized_indicator:
                    if index_is_multi:
                        component_values = param_series.loc[(slice(None), component)]
                    else:
                        cv = param_series.loc[component]
                        component_values = cv if hasattr(cv, "index") and hasattr(cv.index, "get_level_values") else pd.Series([cv])

                    # prevent of making multiple unnecessary ts files
                    if len(set(component_values)) > 1:
                        scenario_data = pd.DataFrame(
                            [component_values.values], columns=component_values.index.get_level_values(0)
                        )

                        timeseries_name = f"{system_name}_{component}_{param}"
                        comp_param_to_scenario_dependent_static_name[(component, param)] = timeseries_name

                        separator = "," if series_file_format == ".csv" else "\t"
                        scenario_data.to_csv(
                            series_dir / Path(f"{timeseries_name}{series_file_format}"),
                            index=False,
                            header=False,
                            sep=separator,
                        )
                    else:
                        component_value = list(set(component_values))[0]
                        # Replace infinite values with large finite numbers to prevent writing .inf or -.inf
                        if pd.isna(component_value):
                            component_value = 0.0
                        elif component_value == float("inf"):
                            component_value = 1e20
                        elif component_value == float("-inf"):
                            component_value = -1e20
          
                        if param == "co2_emissions":
                            print("[GemsStudyWriter] (component, co2_emissions) =", (component, param), "-> value:", component_value, "| component_values:", component_values.tolist())
                        comp_param_to_scenario_dependent_static_name[(component, param)] = component_value

        # [E2E emission_factor] Full static dict for co2_emissions returned to converter
        co2_static = [(k, v) for k, v in comp_param_to_scenario_dependent_static_name.items() if k[1] == "co2_emissions"]
        if co2_static:
            print("[GemsStudyWriter] RETURN comp_param_to_static_name (co2_emissions):", co2_static)

        return comp_param_to_scenario_dependent_timeseries_name, comp_param_to_scenario_dependent_static_name



    def write_optim_config_yml(self) -> None:
        Path(self.study_dir / "systems" / "input" / "model-libraries").mkdir(parents=True, exist_ok=True)
        destination_file = Path(self.study_dir / "systems" / "input" / "optim-config.yml")
        destination_file.touch()

        project_root = Path(__file__).parent.parent
        source_file = project_root / "resources" / "optim-config.yml"
        shutil.copy(source_file, destination_file)
