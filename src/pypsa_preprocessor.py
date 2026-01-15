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
from math import inf

import pandas as pd
from pypsa import Network

from src.utils import StudyType, any_to_float


class PyPSAPreprocessor:
    def __init__(self, pypsa_network: Network, study_type: StudyType):
        self.pypsa_network = pypsa_network
        self.study_type = study_type
        self.pypsa_components = [
            "buses",
            "loads",
            "generators",
            "stores",
            "storage_units",
            "links",
            "lines",
            "transformers",
        ]

    def network_preprocessing(self) -> Network:
        self._pypsa_network_assertion()
        self._add_fictitious_carrier()
        self._rename_buses()
        self._preprocess_pypsa_components()
        return self.pypsa_network

    def _pypsa_network_assertion(self) -> None:
        """Assertion function to keep trace of the limitations of the converter"""
        assert len(self.pypsa_network.investment_periods) == 0
        assert (self.pypsa_network.snapshot_weightings.values == 1.0).all()
        ### PyPSA components : Generators
        if not (all((self.pypsa_network.generators["marginal_cost_quadratic"] == 0))):
            raise ValueError("Converter supports only Generators with linear cost")
        if not (all((self.pypsa_network.generators["active"] == 1))):
            raise ValueError("Converter supports only Generators with active = 1")
        if not (all((self.pypsa_network.generators["committable"] == False))):
            raise ValueError("Converter supports only Generators with commitable = False")
        ### PyPSA components : Loads
        if not (all((self.pypsa_network.loads["active"] == 1))):
            raise ValueError("Converter supports only Loads with active = 1")
        ### PyPSA components : Links
        if not (all((self.pypsa_network.links["active"] == 1))):
            raise ValueError("Converter supports only Links with active = 1")
        ### PyPSA components : Lines
        if not len(self.pypsa_network.lines) == 0:
            raise ValueError("Converter does not support Lines yet")
        ### PyPSA components : Storage Units
        if not (all((self.pypsa_network.links["active"] == 1))):
            raise ValueError("Converter supports only Storage Units with active = 1")
        if not (all((self.pypsa_network.storage_units["sign"] == 1))):
            raise ValueError("Converter supports only Storage Units with sign = 1")
        if not (all((self.pypsa_network.storage_units["cyclic_state_of_charge"] == 1))):
            raise ValueError("Converter supports only Storage Units with cyclic_state_of_charge")
        if not (all((self.pypsa_network.storage_units["marginal_cost_quadratic"] == 0))):
            raise ValueError("Converter supports only Storage Units with linear cost")
        ### PyPSA components : Stores
        if not (all((self.pypsa_network.links["active"] == 1))):
            raise ValueError("Converter supports only Stores with active = 1")
        if not (all((self.pypsa_network.stores["sign"] == 1))):
            raise ValueError("Converter supports only Stores with sign = 1")
        if not (all((self.pypsa_network.stores["e_cyclic"] == 1))):
            raise ValueError("Converter supports only Stores with e_cyclic = True")
        if not (all((self.pypsa_network.stores["marginal_cost_quadratic"] == 0))):
            raise ValueError("Converter supports only Stores with linear cost")
        ### PyPSA components : GlobalConstraint
        for pypsa_model_id in self.pypsa_network.global_constraints.index:
            assert self.pypsa_network.global_constraints.loc[pypsa_model_id, "type"] == "primary_energy"
            assert self.pypsa_network.global_constraints.loc[pypsa_model_id, "carrier_attribute"] == "co2_emissions"

    def _add_fictitious_carrier(self) -> None:
        """Add fictitious carrier to the network"""
        self.pypsa_network.add(
            "Carrier",
            "null",
            co2_emissions=0,
            max_growth=any_to_float(inf),
        )
        self.pypsa_network.carriers["carrier"] = self.pypsa_network.carriers.index.values

    def _rename_buses(self) -> None:
        if self.study_type == StudyType.DETERMINISTIC:
            self._rename_buses_linear_optimal_power_flow()
        elif self.study_type == StudyType.WITH_SCENARIOS:
            self._rename_buses_two_stage_stochastic_optimization()
        else:
            raise ValueError(f"Study type {self.study_type} not supported")

    def _rename_buses_linear_optimal_power_flow(self) -> None:
        """Rename buses for linear optimal power flow"""
        ### Rename PyPSA buses, to delete spaces
        if len(self.pypsa_network.buses) > 0:
            self.pypsa_network.buses.index = self.pypsa_network.buses.index.str.replace(" ", "_")
            for _, val in self.pypsa_network.buses_t.items():
                val.columns = val.columns.str.replace(" ", "_")
        ### Update the 'bus' columns for the different types of PyPSA components
        for component_type in self.pypsa_components:
            df = getattr(self.pypsa_network, component_type)
            if len(df) > 0:
                for col in ["bus", "bus0", "bus1"]:
                    if col in df.columns:
                        df[col] = df[col].str.replace(" ", "_")

        self._rename_buses_in_components()

    def _rename_buses_two_stage_stochastic_optimization(self) -> None:
        """Rename buses for two-stage stochastic optimization studies.
        Handles MultiIndex cases (with scenarios).
        """
        ### Rename PyPSA buses, to delete spaces
        if len(self.pypsa_network.buses) > 0:
            # For MultiIndex: network.buses.index is a MultiIndex (e.g. (scenario, name))
            levels = list(self.pypsa_network.buses.index.levels)
            if len(levels) > 1:
                levels[1] = levels[1].str.replace(" ", "_")
                self.pypsa_network.buses.index = pd.MultiIndex.from_arrays(
                    [
                        self.pypsa_network.buses.index.get_level_values(0),  # scenario name
                        self.pypsa_network.buses.index.get_level_values(1).str.replace(" ", "_"),  # bus name
                    ],
                    names=self.pypsa_network.buses.index.names,
                )

        # Rename columns in network.buses_t (values are always regular Index)
        for _, val in self.pypsa_network.buses_t.items():
            if isinstance(val.columns, pd.MultiIndex):
                level_0 = val.columns.get_level_values(0)
                level_1 = val.columns.get_level_values(1).str.replace(" ", "_")
                val.columns = pd.MultiIndex.from_arrays([level_0, level_1], names=val.columns.names)

        self._rename_buses_in_components()

    def _rename_buses_in_components(self) -> None:
        """Rename buses in all components"""
        for component_type in self.pypsa_components:
            df = getattr(self.pypsa_network, component_type)
            if len(df) > 0:
                for col in ["bus", "bus0", "bus1"]:
                    if col in df.columns:
                        df[col] = df[col].str.replace(" ", "_")

    def _rename_pypsa_component(self, component_type: str) -> None:
        if self.study_type == StudyType.DETERMINISTIC:
            self._rename_pypsa_components_linear_optimal_power_flow(component_type)
        elif self.study_type == StudyType.WITH_SCENARIOS:
            self._rename_pypsa_components_two_stage_stochastic_optimization(component_type)
        else:
            raise ValueError(f"Study type {self.study_type} not supported")

    def _rename_pypsa_components_linear_optimal_power_flow(self, component_type: str) -> None:
        df = getattr(self.pypsa_network, component_type)
        if len(df) == 0:
            return
        ### Rename PyPSA components, to make sure that the names are uniques (used as id in the Gems model)
        prefix = component_type[:-1]
        df.index = prefix + "_" + df.index.str.replace(" ", "_")
        dictionnary = getattr(self.pypsa_network, component_type + "_t")
        for _, val in dictionnary.items():
            val.columns = prefix + "_" + val.columns.str.replace(" ", "_")

    def _rename_pypsa_components_two_stage_stochastic_optimization(self, component_type: str) -> None:
        df = getattr(self.pypsa_network, component_type)
        if len(df) == 0:
            return
        ### Rename PyPSA components, to make sure that the names are uniques (used as id in the Gems model)
        prefix = component_type[:-1]

        # Handle df.index - MultiIndex case (scenario, component_name)
        # Keep level_0 (scenario) unchanged, rename level_1 to match LOPF format
        level_0 = df.index.get_level_values(0)
        level_1_renamed = prefix + "_" + df.index.get_level_values(1).str.replace(" ", "_")
        df.index = pd.MultiIndex.from_arrays([level_0, level_1_renamed], names=df.index.names)

        # Handle component_t columns - MultiIndex case (scenario, component_name)
        dictionnary = getattr(self.pypsa_network, component_type + "_t")
        for _, val in dictionnary.items():
            if isinstance(val.columns, pd.MultiIndex):
                level_0_cols = val.columns.get_level_values(0)
                level_1_cols_renamed = prefix + "_" + val.columns.get_level_values(1).str.replace(" ", "_")
                val.columns = pd.MultiIndex.from_arrays([level_0_cols, level_1_cols_renamed], names=val.columns.names)

    def _fix_capacity_non_extendable_attribute(self, component_type: str, capa_str: str) -> None:
        df = getattr(self.pypsa_network, component_type)
        if len(df) == 0:
            return
        ### Adding min and max capacities to non-extendable objects
        for field in [capa_str + "_min", capa_str + "_max"]:
            df.loc[df[capa_str + "_extendable"] == False, field] = df[capa_str]
            df.loc[df[capa_str + "_extendable"] == False, "capital_cost"] = 0.0

    def _preprocess_pypsa_component(self, component_type: str, non_extendable: bool, attribute_name: str) -> None:
        ### Handling PyPSA objects without carriers

        df = getattr(self.pypsa_network, component_type)
        for comp in df.index:
            if len(df.loc[comp, "carrier"]) == 0:
                df.loc[comp, "carrier"] = "null"
        setattr(
            self.pypsa_network,
            component_type,
            df.join(
                self.pypsa_network.carriers,
                on="carrier",
                how="left",
                rsuffix="_carrier",
            ),
        )

        self._rename_pypsa_component(component_type)
        if non_extendable:
            self._fix_capacity_non_extendable_attribute(component_type, attribute_name)

    def _preprocess_pypsa_components(self) -> None:
        self._preprocess_pypsa_component("loads", False, "/")
        self._preprocess_pypsa_component("generators", True, "p_nom")
        self._preprocess_pypsa_component("stores", True, "e_nom")
        self._preprocess_pypsa_component("storage_units", True, "p_nom")
        self._preprocess_pypsa_component("links", True, "p_nom")
