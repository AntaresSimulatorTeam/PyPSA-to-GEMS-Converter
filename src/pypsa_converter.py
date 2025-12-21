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

from src.pypsa_preprocessor import PyPSAPreprocessor
from src.pypsa_register import PyPSARegister
from src.utils import any_to_float, check_time_series_format, determine_pypsa_study_type

from .models import (
    GemsComponent,
    GemsComponentParameter,
    GemsPortConnection,
    GemsSystem,
    ModelerParameters,
)
from .models.pypsa_model_schema import PyPSAComponentData, PyPSAGlobalConstraintData


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

        for pypsa_components_data in self.pypsa_components_data.values():
            components, connections = self._convert_pypsa_components_of_given_model(pypsa_components_data)
            list_components.extend(components)
            list_connections.extend(connections)

        for pypsa_global_constraint_data in self.pypsa_globalconstraints_data.values():
            (
                components,
                connections,
            ) = self._convert_pypsa_globalconstraint_of_given_model(pypsa_global_constraint_data)
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

    def _convert_pypsa_components_of_given_model(
        self, pypsa_components_data: PyPSAComponentData
    ) -> tuple[list[GemsComponent], list[GemsPortConnection]]:
        """
        Generic function to handle the different PyPSA classes

        """

        self.logger.info(f"Creating objects of type: {pypsa_components_data.gems_model_id}. ")

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

        connections = self._create_gems_connections(
            pypsa_components_data.constant_data,
            pypsa_components_data.pypsa_params_to_gems_connections,
        )

        components = self._create_gems_components(
            pypsa_components_data.constant_data,
            pypsa_components_data.gems_model_id,
            pypsa_components_data.pypsa_params_to_gems_params,
            comp_param_to_timeseries_name,
        )
        return components, connections

    def _convert_pypsa_globalconstraint_of_given_model(
        self, pypsa_gc_data: PyPSAGlobalConstraintData
    ) -> tuple[list[GemsComponent], list[GemsPortConnection]]:
        self.logger.info(f"Creating PyPSA GlobalConstraint of type: {pypsa_gc_data.gems_model_id}. ")
        components = [
            GemsComponent(
                id=pypsa_gc_data.pypsa_name,
                model=f"{self.pypsalib_id}.{pypsa_gc_data.gems_model_id}",
                parameters=[
                    GemsComponentParameter(
                        id="quota",
                        time_dependent=False,
                        scenario_dependent=False,
                        value=pypsa_gc_data.pypsa_constant,
                    )
                ],
            )
        ]
        connections = []
        for component_id, port_id in pypsa_gc_data.gems_components_and_ports:
            connections.append(
                GemsPortConnection(
                    component1=pypsa_gc_data.pypsa_name,
                    port1=pypsa_gc_data.gems_port_id,
                    component2=component_id,
                    port2=port_id,
                )
            )

        return components, connections

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

    def _create_gems_components(
        self,
        constant_data: pd.DataFrame,
        gems_model_id: str,
        pypsa_params_to_gems_params: dict[str, str],
        comp_param_to_timeseries_name: dict[tuple[str, str], str],
    ) -> list[GemsComponent]:
        components = []
        for component in constant_data.index:
            components.append(
                GemsComponent(
                    id=component,
                    model=f"{self.pypsalib_id}.{gems_model_id}",
                    parameters=[
                        GemsComponentParameter(
                            id=pypsa_params_to_gems_params[param],
                            time_dependent=(component, param) in comp_param_to_timeseries_name,
                            scenario_dependent=False,
                            value=(
                                comp_param_to_timeseries_name[(component, param)]
                                if (component, param) in comp_param_to_timeseries_name
                                else any_to_float(constant_data.loc[component, param])
                            ),
                        )
                        for param in pypsa_params_to_gems_params
                    ],
                )
            )
        return components

    def _create_gems_connections(
        self,
        constant_data: pd.DataFrame,
        pypsa_params_to_gems_connections: dict[str, tuple[str, str]],
    ) -> list[GemsPortConnection]:
        connections = []
        for bus_id, (
            model_port,
            bus_port,
        ) in pypsa_params_to_gems_connections.items():
            buses = constant_data[bus_id].values
            for component_id, component in enumerate(constant_data.index):
                connections.append(
                    GemsPortConnection(
                        component1=buses[component_id],
                        port1=bus_port,
                        component2=component,
                        port2=model_port,
                    )
                )
        return connections
