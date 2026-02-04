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
        self._check_converter_limitations()
        self._add_fictitious_carrier()
        self._rename_buses()
        self._preprocess_pypsa_components()
        return self.pypsa_network

    def _check_converter_limitations(self) -> None:
        """Assertion function to keep trace of the limitations of the converter"""
        assert len(self.pypsa_network.investment_periods) == 0
        assert (self.pypsa_network.snapshot_weightings.values == 1.0).all()
        checks = [
        ("generators", "marginal_cost_quadratic", 0, "Generators", "linear cost"),
        ("generators", "active", 1, "Generators", "active = 1"),
        ("generators", "committable", False, "Generators", "commitable = False"),
        ("loads", "active", 1, "Loads", "active = 1"),
        ("links", "active", 1, "Links", "active = 1"),
        ("storage_units", "sign", 1, "Storage Units", "sign = 1"),
        ("storage_units", "cyclic_state_of_charge", 1, "Storage Units", "cyclic_state_of_charge"),
        ("storage_units", "marginal_cost_quadratic", 0, "Storage Units", "linear cost"),
        ("stores", "sign", 1, "Stores", "sign = 1"),
        ("stores", "e_cyclic", 1, "Stores", "e_cyclic = True"),
        ("stores", "marginal_cost_quadratic", 0, "Stores", "linear cost"),
    ]

        for component_type, col, expected, type_label, desc in checks:
            c = getattr(self.pypsa_network.components, component_type)
            if len(c.static) == 0:
                continue
            if not all(c.static[col] == expected):
                raise ValueError(f"Converter supports only {type_label} with {desc}")

        if len(self.pypsa_network.components.lines.static) != 0:
            raise ValueError("Converter does not support Lines yet")
        
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
        """
        Rename buses Handles MultiIndex cases (with scenarios).
        """
        c = self.pypsa_network.components.buses
        if len(c.static) == 0:
            return

        index = c.static.index
        names = index.get_level_values(-1) if isinstance(index, pd.MultiIndex) else index
        rename_map = {name: str(name).replace(" ", "_") for name in names if " " in str(name)}

        if rename_map:
            c.rename_component_names(**rename_map)

    def _rename_pypsa_component(self, component_type: str) -> None:
        """
        Rename PyPSA components to ensure unique names (used as id in the GEMS model)
        by adding prefix and replacing spaces with underscores.
        """
        component = getattr(self.pypsa_network.components, component_type)

        if len(component.static) == 0:
            return

        prefix = component_type[:-1]  # generators->generator, storage_units->storage_unit

        # Build old_name -> new_name mapping
        index = component.static.index
        names = index.get_level_values(-1) #if isinstance(index, pd.MultiIndex) else index
        rename_map = {name: f"{prefix}_{str(name).replace(' ', '_')}" for name in names}

        if not rename_map:
            return

        # Rename static index
        component.static.rename(index=rename_map, inplace=True)

        # Rename dynamic columns (each key in dynamic is an attribute, value is DataFrame)
        for key in component.dynamic:
            df = component.dynamic[key]
            level_vals = df.columns.get_level_values(-1)
            new_vals = level_vals.map(lambda x: rename_map.get(x, x))
            new_columns = pd.MultiIndex.from_arrays(
                [df.columns.get_level_values(i) for i in range(df.columns.nlevels - 1)]
                + [new_vals],
                names=df.columns.names,
            )
            component.dynamic[key].columns = new_columns


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
        # Ensure scalar carrier (MultiIndex + join can misalign; map is reliable)
        carrier_series = df["carrier"].apply(_carrier_scalar)
        carrier_series = carrier_series.where(carrier_series != "", "null")
        df["carrier"] = carrier_series

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

        setattr(self.pypsa_network, component_type, joined)

        self._rename_pypsa_component(component_type)
        
        if non_extendable:
            self._fix_capacity_non_extendable_attribute(component_type, attribute_name)

    def _preprocess_pypsa_components(self) -> None:
        self._preprocess_pypsa_component("loads", False, "/")
        self._preprocess_pypsa_component("generators", True, "p_nom")
        self._preprocess_pypsa_component("stores", True, "e_nom")
        self._preprocess_pypsa_component("storage_units", True, "p_nom")
        self._preprocess_pypsa_component("links", True, "p_nom")
