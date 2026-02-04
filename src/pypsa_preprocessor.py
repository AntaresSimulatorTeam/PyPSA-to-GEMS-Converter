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
from math import inf

import pandas as pd
from pypsa import Network

from src.utils import any_to_float


def _carrier_scalar(val) -> str:
    """Extract scalar carrier name; PyPSA with scenarios may store carrier as array per row."""
    if val is None or (isinstance(val, float) and pd.isna(val)):
        return "null"
    if isinstance(val, str):
        return val
    if hasattr(val, "__len__") and not isinstance(val, str):
        if len(val) == 0:
            return "null"
        return _carrier_scalar(val[0])
    return str(val)


class PyPSAPreprocessor:
    def __init__(self, pypsa_network: Network):
        self.pypsa_network = pypsa_network
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
            # Set capital_cost to 0.0 for all non-extendable components (capacity is fixed)
            df.loc[df[capa_str + "_extendable"] == False, "capital_cost"] = 0.0

    def _preprocess_pypsa_component(self, component_type: str, non_extendable: bool, attribute_name: str) -> None:
        df = getattr(self.pypsa_network, component_type)
        # Ensure scalar carrier (MultiIndex + join can misalign; map is reliable)
        carrier_series = df["carrier"].apply(_carrier_scalar)
        carrier_series = carrier_series.where(carrier_series != "", "null")
        df["carrier"] = carrier_series

        if component_type in ("generators", "stores", "storage_units") and len(df) > 0:
            print("[Preprocessor]", component_type, "before join: carrier =", df["carrier"].tolist())
        joined = df.join(
            self.pypsa_network.carriers,
            on="carrier",
            how="left",
            rsuffix="_carrier",
        )
        # Set co2_emissions from scalar carrier map (join with MultiIndex left can yield NaN).
        # Prefer snapshot taken before set_scenarios; PyPSA may overwrite carrier co2_emissions after expansion.
        co2_map = getattr(self.pypsa_network, "_carrier_co2_snapshot", None)
        if co2_map is None and "co2_emissions" in self.pypsa_network.carriers.columns:
            carriers_df = self.pypsa_network.carriers
            idx = carriers_df.index
            if isinstance(idx, pd.MultiIndex):
                names = idx.get_level_values(-1)
            else:
                names = idx
            co2_map = {}
            for i in range(len(carriers_df)):
                k = str(names[i])
                if k not in co2_map:
                    co2_map[k] = float(carriers_df["co2_emissions"].iloc[i])
        if co2_map is not None:
            co2_map = dict(co2_map)
            co2_map.setdefault("null", 0.0)
            joined["co2_emissions"] = carrier_series.astype(str).map(co2_map).fillna(0.0)
        elif "co2_emissions_carrier" in joined.columns:
            joined["co2_emissions"] = joined["co2_emissions_carrier"]
        if "co2_emissions_carrier" in joined.columns:
            joined = joined.drop(columns=["co2_emissions_carrier"])

        if component_type in ("generators", "stores", "storage_units") and len(joined) > 0:
            if "co2_emissions" in joined.columns:
                print("[Preprocessor]", component_type, "after join: co2_emissions =", joined["co2_emissions"].tolist())
            else:
                print("[Preprocessor]", component_type, "after join: no co2_emissions column, columns =", list(joined.columns))
        setattr(self.pypsa_network, component_type, joined)

        self._rename_pypsa_component(component_type)
        # if component is non extendable that
        if non_extendable:
            self._fix_capacity_non_extendable_attribute(component_type, attribute_name)

    def _preprocess_pypsa_components(self) -> None:
        self._preprocess_pypsa_component("loads", False, "/")
        self._preprocess_pypsa_component("generators", True, "p_nom")
        self._preprocess_pypsa_component("stores", True, "e_nom")
        self._preprocess_pypsa_component("storage_units", True, "p_nom")
        self._preprocess_pypsa_component("links", True, "p_nom")
