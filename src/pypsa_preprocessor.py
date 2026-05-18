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
from typing import Any

import pandas as pd
from pypsa import Network

from src.utils import any_to_float


def _carrier_scalar(val: Any) -> str:
    """Extract scalar carrier name (PyPSA with scenarios store carrier as array per row)."""
    if val is None or (isinstance(val, float) and pd.isna(val)):
        return "null"
    if isinstance(val, str):
        return val
    try:
        if len(val) == 0:
            return "null"
        return _carrier_scalar(val[0])
    except Exception:
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

        ### PyPSA components : GlobalConstraint
        for pypsa_model_id in self.pypsa_network.global_constraints.index:
            assert self.pypsa_network.global_constraints.loc[pypsa_model_id, "type"] == "primary_energy"
            assert self.pypsa_network.global_constraints.loc[pypsa_model_id, "carrier_attribute"] == "co2_emissions"

    def _add_fictitious_carrier(self) -> None:
        """Add fictitious carrier (co2_emissions=0) for components with no carrier.

        n.add() on a scenarios network collapses the MultiIndex to a flat Index with
        tuple keys, so we insert directly into the DataFrame instead.
        """
        carriers_df = self.pypsa_network.carriers
        idx = carriers_df.index
        existing = idx.get_level_values(-1) if isinstance(idx, pd.MultiIndex) else idx
        if "null" in existing:
            return
        if isinstance(idx, pd.MultiIndex):
            scenarios = idx.get_level_values(0).unique()
            null_data = {
                col: ("" if carriers_df[col].dtype == object else 0.0)
                for col in carriers_df.columns
            }
            null_data["co2_emissions"] = 0.0
            null_data["max_growth"] = any_to_float(inf)
            null_idx = pd.MultiIndex.from_tuples([(s, "null") for s in scenarios], names=idx.names)
            null_df = pd.DataFrame(null_data, index=null_idx)
            self.pypsa_network.carriers = pd.concat([carriers_df, null_df])
        else:
            self.pypsa_network.add("Carrier", "null", co2_emissions=0, max_growth=any_to_float(inf))

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
        names = index.get_level_values(-1)  # if isinstance(index, pd.MultiIndex) else index
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
                [df.columns.get_level_values(i) for i in range(df.columns.nlevels - 1)] + [new_vals],
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

    def _carrier_co2_by_scenario(self, carrier_series: pd.Series) -> pd.Series:
        """Return co2_emissions for each component row, preserving per-scenario variation."""
        carriers = self.pypsa_network.carriers
        if "co2_emissions" not in carriers.columns:
            return pd.Series(0.0, index=carrier_series.index)
        if isinstance(carriers.index, pd.MultiIndex):
            co2_col = carriers["co2_emissions"]
            scenarios = carrier_series.index.get_level_values(0)
            return pd.Series(
                [float(co2_col.get((s, c), 0.0)) for s, c in zip(scenarios, carrier_series)],
                index=carrier_series.index,
            )
        co2_map: dict[str, float] = carriers["co2_emissions"].to_dict()
        co2_map.setdefault("null", 0.0)
        return carrier_series.map(co2_map).fillna(0.0)

    def _preprocess_pypsa_component(self, component_type: str, non_extendable: bool, attribute_name: str) -> None:
        ### Handling PyPSA objects without carriers
        df = getattr(self.pypsa_network, component_type)
        carrier_series = df["carrier"].apply(_carrier_scalar)
        carrier_series = carrier_series.where(carrier_series != "", "null")
        df["carrier"] = carrier_series

        df["co2_emissions"] = self._carrier_co2_by_scenario(carrier_series)

        setattr(self.pypsa_network, component_type, df)

        self._rename_pypsa_component(component_type)

        if non_extendable:
            self._fix_capacity_non_extendable_attribute(component_type, attribute_name)

    def _add_bus_theta_bounds(self) -> None:
        """Add theta angle bounds to buses for DC LOPF. Fix reference bus angle to 0."""
        if len(self.pypsa_network.buses) == 0:
            return

        self.pypsa_network.determine_network_topology()

        # Re-fetch after determine_network_topology(), which may replace the internal DataFrame
        buses_df = self.pypsa_network.components.buses.static

        buses_df["theta_min"] = float("-inf")
        buses_df["theta_max"] = float("inf")

        index = buses_df.index
        names = index.get_level_values(-1) if isinstance(index, pd.MultiIndex) else index

        slack_buses: list[str] = []
        for name in dict.fromkeys(names):
            mask = (index.get_level_values(-1) == name) if isinstance(index, pd.MultiIndex) else (index == name)
            if buses_df.loc[mask, "control"].iloc[0] == "Slack":
                slack_buses.append(str(name))

        if not slack_buses:
            slack_buses = [str(names[0])]

        for slack_bus in slack_buses:
            mask = (index.get_level_values(-1) == slack_bus) if isinstance(index, pd.MultiIndex) else (index == slack_bus)
            buses_df.loc[mask, "theta_min"] = 0.0
            buses_df.loc[mask, "theta_max"] = 0.0

    def _add_modular_flag(self, component_type: str) -> None:
        """Compute modular expansion flag and ensure s_nom_mod is positive."""
        df = getattr(self.pypsa_network, component_type)
        if len(df) == 0:
            return
        df["modular"] = (df["s_nom_extendable"] & (df["s_nom_mod"] > 0)).astype(float)
        df.loc[df["s_nom_mod"] == 0, "s_nom_mod"] = 1.0

    def _preprocess_pypsa_components(self) -> None:
        self._preprocess_pypsa_component("loads", False, "/")
        self._preprocess_pypsa_component("generators", True, "p_nom")
        self._preprocess_pypsa_component("stores", True, "e_nom")
        self._preprocess_pypsa_component("storage_units", True, "p_nom")
        self._preprocess_pypsa_component("links", True, "p_nom")
        self._add_bus_theta_bounds()
        if len(self.pypsa_network.lines) > 0 or len(self.pypsa_network.transformers) > 0:
            self.pypsa_network.calculate_dependent_values()
        if len(self.pypsa_network.lines) > 0:
            self._add_modular_flag("lines")
            self._rename_pypsa_component("lines")
            self._fix_capacity_non_extendable_attribute("lines", "s_nom")
        if len(self.pypsa_network.transformers) > 0:
            self._add_modular_flag("transformers")
            self._rename_pypsa_component("transformers")
            self._fix_capacity_non_extendable_attribute("transformers", "s_nom")
