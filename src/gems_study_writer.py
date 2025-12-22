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
        pypsa_components_data: PyPSAComponentData,
        system_name: str,
        series_file_format: str,
    ) -> dict[tuple[str, str], str]:
        if self.study_type == StudyType.LINEAR_OPTIMAL_POWER_FLOW:  # TODO: add two-stage stochastic study type
            # List of params that may be time-dependent in the pypsa model, among those we want to keep
            time_dependent_params = set(
                pypsa_components_data.pypsa_params_to_gems_params
            ).intersection(  # pecific for linear optimal power flow study type
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
            return comp_param_to_timeseries_name
        return dict[
            tuple[str, str], str
        ]()  # default because of mypy,for now until we adopt 2 stage stochastic study type

    def write_optim_config_yml(self) -> None:
        pass
