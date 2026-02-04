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
import math
from typing import Any, cast

import pandas as pd

from src.models.gems_system_yml_schema import GemsComponent, GemsComponentParameter, GemsPortConnection
from src.models.pypsa_model_schema import PyPSAComponentData, PyPSAGlobalConstraintData
from src.utils import StudyType


def _sanitize_parameter_value(gems_param_id: str, value: Any) -> Any:
    """Replace NaN/None numeric values with 0 (e.g. emission_factor with no carrier, or missing optional params)."""
    if isinstance(value, str):
        return value
    if value is None:
        return 0.0
    if isinstance(value, float) and math.isnan(value):
        return 0.0
    try:
        if pd.isna(value):
            return 0.0
    except (TypeError, ValueError):
        pass
    return value


def _to_gems_constraint_id(pypsa_name: str | tuple) -> str:
    """Convert global constraint name to string id (WITH_SCENARIOS uses MultiIndex tuples). Use name only, no scenario prefix."""
    if isinstance(pypsa_name, tuple):
        return str(pypsa_name[1])
    return str(pypsa_name)


def _to_gems_component_id(comp_id: str | tuple) -> str:
    """Convert component id to string; with scenarios use name part only to match system components."""
    if isinstance(comp_id, tuple):
        return str(comp_id[1])
    return str(comp_id)


class GemsModelBuilder:
    def __init__(self, pypsalib_id: str, study_type: StudyType):
        self.pypsalib_id = pypsalib_id
        self.logger = logging.getLogger(__name__)
        self.study_type = study_type

    def _convert_pypsa_globalconstraint(
        self, pypsa_gc_data: PyPSAGlobalConstraintData
    ) -> tuple[list[GemsComponent], list[GemsPortConnection]]:
        """
        Convert PyPSA GlobalConstraint
        This function is independent of study type and process
        """

        self.logger.info(f"Creating PyPSA GlobalConstraint of type: {pypsa_gc_data.gems_model_id}. ")
        constraint_id = _to_gems_constraint_id(pypsa_gc_data.pypsa_name)
        components = [
            GemsComponent(
                id=constraint_id,
                model=f"{self.pypsalib_id}.{pypsa_gc_data.gems_model_id}",
                parameters=[
                    GemsComponentParameter(
                        id="quota",
                        time_dependent=False,
                        scenario_dependent=False,
                        value=_sanitize_parameter_value("quota", pypsa_gc_data.pypsa_constant),
                    )
                ],
            )
        ]
        connections = []
        for component_id, port_id in pypsa_gc_data.gems_components_and_ports:
            connections.append(
                GemsPortConnection(
                    component1=constraint_id,
                    port1=pypsa_gc_data.gems_port_id,
                    component2=_to_gems_component_id(component_id),
                    port2=port_id,
                )
            )

        return components, connections

    def _create_gems_components(
        self,
        constant_data: pd.DataFrame,
        gems_model_id: str,
        pypsa_params_to_gems_params: dict[str, str],
        comp_param_to_timeseries_name: dict[tuple[str, str], list[str | bool]],
        comp_param_to_static_name: dict[tuple[str, str], str | float],
    ) -> list[GemsComponent]:
        # Check if index is MultiIndex (should be for TWO_STAGE_STOCHASTIC)
        components = []
        # Get unique component names from level 1 (component name level)
        component_names = constant_data.index.get_level_values(1).unique()

        for component in component_names:
            # [E2E emission_factor] 3. What model builder looks up and writes for emission_factor
            if "co2_emissions" in pypsa_params_to_gems_params:
                raw_val = comp_param_to_static_name.get((component, "co2_emissions"))
                final_val = _sanitize_parameter_value("emission_factor", raw_val)
                print("[GemsModelBuilder] 3. (component, co2_emissions) =", (component, "co2_emissions"), "-> raw =", raw_val, "-> emission_factor value =", final_val)
            components.append(
                GemsComponent(
                    id=component,
                    model=f"{self.pypsalib_id}.{gems_model_id}",
                    parameters=[
                        GemsComponentParameter(
                            id=pypsa_params_to_gems_params[param],
                            time_dependent=(component, param) in comp_param_to_timeseries_name,
                            scenario_dependent=(
                                (
                                    (component, param) in comp_param_to_static_name
                                    and isinstance(
                                        comp_param_to_static_name[(component, param)],
                                        str,
                                    )
                                )
                                or (
                                    (component, param) in comp_param_to_timeseries_name
                                    and comp_param_to_timeseries_name[(component, param)][1]
                                )
                            ),
                            value=_sanitize_parameter_value(
                                pypsa_params_to_gems_params[param],
                                comp_param_to_timeseries_name[(component, param)][0]
                                if (component, param) in comp_param_to_timeseries_name
                                else comp_param_to_static_name.get((component, param)),
                            ),
                        )
                        for param in pypsa_params_to_gems_params
                    ],
                )
            )
        return components


    def _create_gems_connections(
        self, constant_data: pd.DataFrame, pypsa_params_to_gems_connections: dict[str, tuple[str, str]]
    ) -> list[GemsPortConnection]:
        connections = []
        for bus_id, (model_port, bus_port) in pypsa_params_to_gems_connections.items():
            buses = constant_data[bus_id].values

            component_names = constant_data.index.get_level_values(1).unique()
            for component in component_names:
                # Get the first index position for this component (use first scenario)
                component_indices = constant_data.index.get_level_values(1) == component
                first_idx = component_indices.argmax()
                connections.append(
                    GemsPortConnection(
                        component1=buses[first_idx],
                        port1=bus_port,
                        component2=component,
                        port2=model_port,
                    )
                )
        return connections

    def convert_pypsa_components_of_given_model(
        self,
        pypsa_components_data: PyPSAComponentData,
        comp_param_to_timeseries_name: dict[tuple[str, str], str | list[str | bool]],
        comp_param_to_static_name: dict[tuple[str, str], str | float],
    ) -> tuple[list[GemsComponent], list[GemsPortConnection]]:
        """
        Generic function to handle the different PyPSA classes
        """
        self.logger.info(f"Creating objects of type: {pypsa_components_data.gems_model_id}. ")

        connections = self._create_gems_connections(
            pypsa_components_data.constant_data,
            pypsa_components_data.pypsa_params_to_gems_connections,
        )

        components = self._create_gems_components(
            pypsa_components_data.constant_data,
            pypsa_components_data.gems_model_id,
            pypsa_components_data.pypsa_params_to_gems_params,
            comp_param_to_timeseries_name,
            comp_param_to_static_name,
        )
        return components, connections
