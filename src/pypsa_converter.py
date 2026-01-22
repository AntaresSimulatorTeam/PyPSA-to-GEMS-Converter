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
import logging
from pathlib import Path

from pypsa import Network

from src.gems_model_builder import GemsModelBuilder
from src.gems_study_writer import GemsStudyWriter
from src.pypsa_preprocessor import PyPSAPreprocessor
from src.pypsa_register import PyPSARegister
from src.utils import StudyType, check_time_series_format, determine_pypsa_study_type


class PyPSAStudyConverter:
    def __init__(
        self,
        pypsa_network: Network,
        logger: logging.Logger,
        study_dir: Path,
        series_file_format: str,
        solver_name: str = "highs",
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
        self.solver_name = solver_name

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

        gems_study_writer = GemsStudyWriter(self.study_dir, self.study_type)
        self.logger.info("Copying library yml file to study directory")
        gems_study_writer.copy_library_yml()

        gems_model_builder = GemsModelBuilder(self.pypsalib_id, self.study_type)

        for pypsa_components_data in self.pypsa_components_data.values():
            # We test whether the keys of the conversion dictionary are allowed in the PyPSA model : all authorized parameters are columns in the constant data frame (even though they are specified as time-varying values in the time-varying data frame)
            pypsa_components_data.check_params_consistency()

            # Save time series and memorize the time-dependent parameters, also save static scenarized parameters
            comp_param_to_timeseries_name, comp_param_to_static_name = gems_study_writer.write_and_register_timeseries(
                pypsa_components_data.time_dependent_data,
                pypsa_components_data.constant_data,
                pypsa_components_data,
                self.system_name,
                self.series_file_format,
            )
            components, connections = gems_model_builder.convert_pypsa_components_of_given_model(
                pypsa_components_data, comp_param_to_timeseries_name, comp_param_to_static_name or {}
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
        gems_study_writer.write_modeler_parameters_yml(len(self.pypsa_network.snapshots) - 1, self.solver_name)
        if self.study_type == StudyType.WITH_SCENARIOS:
            gems_study_writer.write_optim_config_yml()
        self.logger.info("Study conversion completed!")
