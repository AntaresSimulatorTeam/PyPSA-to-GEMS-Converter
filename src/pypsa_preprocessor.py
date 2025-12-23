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
        if self.study_type == StudyType.LINEAR_OPTIMAL_POWER_FLOW:
            self._rename_buses_linear_optimal_power_flow()
        else:
            raise ValueError(f"Study type {self.study_type} not supported")

    def _rename_buses_linear_optimal_power_flow(self) -> None:
        """Rename buses for linear optimal power flow"""
        # #TODO: This function needs to be adapted to the study type
        # #Current: implementation is only for linear optimal power flow
        #
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

    def _rename_pypsa_component(self, component_type: str) -> None:
        if self.study_type == StudyType.LINEAR_OPTIMAL_POWER_FLOW:
            self._rename_pypsa_components_linear_optimal_power_flow(component_type)
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
