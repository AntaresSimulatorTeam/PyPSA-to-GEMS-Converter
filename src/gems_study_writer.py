# Copyright (c) 2025, RTE (https://www.rte-france.com)
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
from src.utils import StudyType


class GemsStudyWriter:
    def __init__(self, study_dir: Path, study_type: StudyType):
        self.study_dir = study_dir
        self.study_type = study_type

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

    def write_modeler_parameters_yml(self, last_time_step: int) -> None:
        ModelerParameters(
            solver="highs",
            solver_logs=False,
            solver_parameters="THREADS 1",
            no_output=False,
            first_time_step=0,
            last_time_step=last_time_step,
        ).to_yml(self.study_dir / "systems" / "parameters.yml")

    def write_and_register_timeseries(
        self,
        time_dependent_data: dict[str, pd.DataFrame],
        constant_data: pd.DataFrame,
        pypsa_components_data: PyPSAComponentData,
        system_name: str,
        series_file_format: str,
    ) -> tuple[dict[tuple[str, str], str], dict[tuple[str, str], str] | None]:
        if self.study_type == StudyType.DETERMINISTIC:
            return self.write_and_register_timeseries_linear_optimal_power_flow(
                time_dependent_data, pypsa_components_data, system_name, series_file_format
            )
        elif self.study_type == StudyType.WITH_SCENARIOS:
            return self.write_and_register_time_series_two_stage_stochastic(
                time_dependent_data, constant_data, pypsa_components_data, system_name, series_file_format
            )
        else:
            raise ValueError(f"Study type {self.study_type} not supported")

    def write_and_register_time_series_two_stage_stochastic(
        self,
        time_dependent_data: dict[str, pd.DataFrame],
        constant_data: pd.DataFrame,
        pypsa_components_data: PyPSAComponentData,
        system_name: str,
        series_file_format: str,
    ) -> tuple[dict[tuple[str, str], str], dict[tuple[str, str], str]]:
        scenarized_time_dependent_params = set(pypsa_components_data.pypsa_params_to_gems_params).intersection(
            set(pypsa_components_data.time_dependent_data.keys())
        )

        comp_param_to_scenario_dependent_timeseries_name = dict()
        comp_param_static_scenarized_indicator = set()

        series_dir = self.study_dir / "systems" / "input" / "data-series"

        if scenarized_time_dependent_params:
            series_dir.mkdir(parents=True, exist_ok=True)

        for param in scenarized_time_dependent_params:
            param_df = time_dependent_data[param]
            component_names = param_df.columns.get_level_values(1).unique()
            for component in component_names:
                component_data = param_df.loc[:, (slice(None), component)]

                comp_param_static_scenarized_indicator.add((component, param))

                timeseries_name = f"{system_name}_{component}_{param}"

                comp_param_to_scenario_dependent_timeseries_name[(component, param)] = timeseries_name

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
        comp_param_to_scenario_dependent_static_name = dict()

        for param in scenarized_static_params:
            param_series = constant_data[param]
            component_names = param_series.index.get_level_values(1).unique()
            for component in component_names:
                if (component, param) not in comp_param_static_scenarized_indicator:
                    component_values = param_series.loc[(slice(None), component)]

        
                    #prevent of making multiple unnecessary ts files
                    if len(set(component_values)) > 1:                        
                        scenario_data = pd.DataFrame(
                            [component_values.values], columns=component_values.index.get_level_values(0)
                        )
                    
                        comp_param_to_scenario_dependent_static_name[(component, param)] = f"{system_name}_{component}_{param}" # ts-name
    
                        separator = "," if series_file_format == ".csv" else "\t"
                        scenario_data.to_csv(
                            series_dir / Path(f"{timeseries_name}{series_file_format}"),
                            index=False,
                            header=False,
                            sep=separator,
                        )
                    else:
                        component_value = list(set(component_values))[0]
                        #prevent all values from -inf and inf
                        if component_value == float("inf"):
                            component_value = 1e20
                        if component_value == float("-inf"):
                            component_value = -1e20
                        comp_param_to_scenario_dependent_static_name[(component, param)] = component_value

        return comp_param_to_scenario_dependent_timeseries_name, comp_param_to_scenario_dependent_static_name

    def write_and_register_timeseries_linear_optimal_power_flow(
        self,
        time_dependent_data: dict[str, pd.DataFrame],
        pypsa_components_data: PyPSAComponentData,
        system_name: str,
        series_file_format: str,
    ) -> tuple[dict[tuple[str, str], str], None]:
        # List of params that may be time-dependent in the pypsa model, among those we want to keep
        time_dependent_params = set(
            pypsa_components_data.pypsa_params_to_gems_params
        ).intersection(  # specific for linear optimal power flow study type
            set(pypsa_components_data.time_dependent_data.keys())
        )

        comp_param_to_timeseries_name = dict()
        series_dir = self.study_dir / "systems" / "input" / "data-series"

        if time_dependent_params:
            series_dir.mkdir(parents=True, exist_ok=True)

        for param in time_dependent_params:
            param_df = time_dependent_data[param]
            for component in param_df.columns:
                timeseries_name = system_name + "_" + component + "_" + param

                comp_param_to_timeseries_name[(component, param)] = timeseries_name

                separator = "," if series_file_format == ".csv" else "\t"
                param_df[[component]].to_csv(
                    series_dir / Path(timeseries_name + series_file_format),
                    index=False,
                    header=False,
                    sep=separator,
                )
        return comp_param_to_timeseries_name, None

    def write_optim_config_yml(self) -> None:
        Path(self.study_dir / "systems" / "input" / "model-libraries").mkdir(parents=True, exist_ok=True)
        destination_file = Path(self.study_dir / "systems" / "input" / "optim-config.yml")
        destination_file.touch()

        project_root = Path(__file__).parent.parent
        source_file = project_root / "resources" / "optim-config.yml"
        shutil.copy(source_file, destination_file)
