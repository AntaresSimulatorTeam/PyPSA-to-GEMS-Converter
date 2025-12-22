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
import logging
import shutil
from pathlib import Path

import pandas as pd
from pypsa import Network

from src.gems_model_builder import GemsModelBuilder
from src.pypsa_preprocessor import PyPSAPreprocessor
from src.pypsa_register import PyPSARegister
from src.utils import check_time_series_format, determine_pypsa_study_type

from .models import (
    GemsSystem,
    ModelerParameters,
)


class PyPSAStudyConverter:
    def __init__(
        self,
        pypsa_network: Network,
        logger: logging.Logger,
        study_dir: Path,
        series_file_format: str,
    ):
        """
        Initialize processor
        """
        self.logger = logger
        self.study_dir = study_dir
        self.pypsa_network = pypsa_network
        self.pypsalib_id = "pypsa_models"
        self.system_name = pypsa_network.name
        self.series_file_format = check_time_series_format(series_file_format)
        self.study_type = determine_pypsa_study_type(self.pypsa_network)

        # Preprocess the network
        self.pypsa_network = PyPSAPreprocessor(self.pypsa_network, self.study_type).network_preprocessing()
        # Register the PyPSA components and global constraints
        self.pypsa_components_data, self.pypsa_globalconstraints_data = PyPSARegister(
            self.pypsa_network, self.study_type
        ).register()

    def to_gems_study(self) -> None:
        """Main function, to export PyPSA as Gems study"""

        self.logger.info("Study conversion started")
        list_components, list_connections = [], []

        Path(self.study_dir / "systems" / "input" / "model-libraries").mkdir(parents=True, exist_ok=True)
        destination_file = Path(self.study_dir / "systems" / "input" / "model-libraries" / "pypsa_models.yml")
        destination_file.touch()

        project_root = Path(__file__).parent.parent
        source_file = project_root / "resources" / "pypsa_models" / "pypsa_models.yml"
        shutil.copy(source_file, destination_file)

        gems_model_builder = GemsModelBuilder(self.pypsalib_id, self.study_type)

        for pypsa_components_data in self.pypsa_components_data.values():
            # We test whether the keys of the conversion dictionary are allowed in the PyPSA model : all authorized parameters are columns in the constant data frame (even though they are specified as time-varying values in the time-varying data frame)
            pypsa_components_data.check_params_consistency()

            # List of params that may be time-dependent in the pypsa model, among those we want to keep
            time_dependent_params = set(pypsa_components_data.pypsa_params_to_gems_params).intersection(
                set(pypsa_components_data.time_dependent_data.keys())
            )
            # Save time series and memorize the time-dependent parameters
            comp_param_to_timeseries_name = self._write_and_register_timeseries(
                pypsa_components_data.time_dependent_data, time_dependent_params
            )
            components, connections = gems_model_builder._convert_pypsa_components_of_given_model(
                pypsa_components_data, comp_param_to_timeseries_name
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

        gems_system = GemsSystem(
            id=system_id,
            nodes=[],
            components=list_components,
            connections=list_connections,
            model_libraries=self.pypsalib_id,
            area_connections=None,
        )
        gems_system.to_yaml(self.study_dir / "systems" / "input" / "system.yml")

        modeler_parameters = ModelerParameters(
            solver="highs",
            solver_logs=False,
            solver_parameters="THREADS 1",
            no_output=False,
            first_time_step=0,
            last_time_step=len(self.pypsa_network.snapshots) - 1,
        )
        modeler_parameters.to_yaml(self.study_dir / "systems" / "parameters.yml")

        self.logger.info("Study conversion completed!")

    def _write_and_register_timeseries(
        self,
        time_dependent_data: dict[str, pd.DataFrame],
        time_dependent_params: set[str],
    ) -> dict[tuple[str, str], str]:
        comp_param_to_timeseries_name = dict()
        series_dir = self.study_dir / "systems" / "input" / "data-series"

        if time_dependent_params:
            series_dir.mkdir(parents=True, exist_ok=True)

        for param in time_dependent_params:
            param_df = time_dependent_data[param]
            for component in param_df.columns:
                timeseries_name = self.system_name + "_" + component + "_" + param

                comp_param_to_timeseries_name[(component, param)] = timeseries_name

                separator = "," if self.series_file_format == ".csv" else "\t"
                param_df[[component]].to_csv(
                    series_dir / Path(timeseries_name + self.series_file_format),
                    index=False,
                    header=False,
                    sep=separator,
                )
        return comp_param_to_timeseries_name
