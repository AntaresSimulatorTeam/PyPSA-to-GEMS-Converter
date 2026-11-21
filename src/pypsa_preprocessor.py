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
from math import inf
from typing import Any

import numpy as np
import pandas as pd
from pypsa import Network
from pypsa.costs import periodized_cost
from scipy.sparse import coo_matrix
from scipy.sparse.csgraph import connected_components

from src.converter_limitations import (
    MAX_MULTIPORT_BUSES,
    check_converter_limitations,
    primary_energy_attribute,
)
from src.time_granularity import add_standing_efficiency, add_time_weights, write_component_frame
from src.utils import PYPSA_CONVERTER_MAX_FLOAT, any_to_float

logger = logging.getLogger("pypsa_to_gems_converter.preprocessor")

# Component types that have an emission_factor parameter in the GEMS model.
# Add a type here when its GEMS model gains emission_factor.
_EMISSION_FACTOR_COMPONENTS: frozenset[str] = frozenset({"generators", "stores", "storage_units"})

# Nominal (capacity) attribute of each component type with a capacity variable.
NOMINAL_ATTRS: dict[str, str] = {
    "generators": "p_nom",
    "links": "p_nom",
    "processes": "p_nom",
    "storage_units": "p_nom",
    "stores": "e_nom",
    "lines": "s_nom",
    "transformers": "s_nom",
}

# Component types with ramp limits in PyPSA, and their PyPSA class name.
RAMP_COMPONENTS: dict[str, str] = {"generators": "Generator", "links": "Link", "processes": "Process"}

# GEMS id prefix of each component type.
_PREFIXES: dict[str, str] = {"processes": "process"}


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
            "processes",
            "lines",
            "transformers",
        ]
        # {component_type: {gems param: shared data series key}} for parameters shared by every component
        # of a type (time-varying snapshot weightings), filled by network_preprocessing().
        self.shared_params: dict[str, dict[str, str]] = {}

    def network_preprocessing(self) -> Network:
        check_converter_limitations(self.pypsa_network)
        self._add_fictitious_carrier()
        self._rename_buses()
        self._pin_nominal_set()
        self._apply_periodized_cost()
        self._preprocess_pypsa_components()
        return self.pypsa_network

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
            null_data = {col: ("" if carriers_df[col].dtype == object else 0.0) for col in carriers_df.columns}
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
            # Global constraints may name a bus (tech_capacity_expansion_limit): PyPSA does not rename it.
            glcs = self.pypsa_network.global_constraints
            if "bus" in glcs.columns:
                glcs["bus"] = glcs["bus"].replace(rename_map)

    def _rename_pypsa_component(self, component_type: str) -> None:
        """
        Rename PyPSA components to ensure unique names (used as id in the GEMS model)
        by adding prefix and replacing spaces with underscores.
        """
        component = getattr(self.pypsa_network.components, component_type)

        if len(component.static) == 0:
            return

        # generators->generator, storage_units->storage_unit, processes->process
        prefix = _PREFIXES.get(component_type, component_type[:-1])

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

    def _pin_nominal_set(self) -> None:
        """Fix the capacity of extendable components with *_nom_set, as PyPSA's define_fixed_nominal_constraints.

        *_nom_set on a non-extendable component is rejected by check_converter_limitations (PyPSA fails on it).
        """
        for component_type, nom in NOMINAL_ATTRS.items():
            df = getattr(self.pypsa_network, component_type)
            set_col = f"{nom}_set"
            if len(df) == 0 or set_col not in df.columns:
                continue
            mask = df[f"{nom}_extendable"].astype(bool) & df[set_col].notna()
            if not mask.any():
                continue
            df.loc[mask, f"{nom}_min"] = df.loc[mask, set_col]
            df.loc[mask, f"{nom}_max"] = df.loc[mask, set_col]

    def _apply_periodized_cost(self) -> None:
        """Replace capital_cost with PyPSA's periodized cost (overnight_cost annuity and fom_cost, PyPSA >= 1.1.0).

        PyPSA charges periodized_cost * capacity for extendable components only; non-extendable components get
        capital_cost = 0 in _fix_capacity_non_extendable_attribute.
        """
        nyears = float(self.pypsa_network.snapshot_weightings["objective"].sum() / 8760.0)
        for component_type in NOMINAL_ATTRS:
            df = getattr(self.pypsa_network, component_type)
            if len(df) == 0:
                continue
            df["capital_cost"] = periodized_cost(
                capital_cost=df["capital_cost"],
                overnight_cost=df["overnight_cost"],
                discount_rate=df["discount_rate"],
                lifetime=df["lifetime"],
                fom_cost=df["fom_cost"] if "fom_cost" in df.columns else None,
                nyears=nyears,
            )

    def _fix_capacity_non_extendable_attribute(self, component_type: str, capa_str: str) -> None:
        df = getattr(self.pypsa_network, component_type)
        if len(df) == 0:
            return
        ### Adding min and max capacities to non-extendable objects
        fixed = df[capa_str + "_extendable"] == False
        # A fixed infinite capacity (e.g. PyPSA-Eur's "co2 atmosphere" store, e_nom = inf) would become a variable
        # fixed at +1e20, which solvers read as an infinite lower bound: clamp it to the converter's maximum float.
        capacity = df[capa_str].clip(-PYPSA_CONVERTER_MAX_FLOAT, PYPSA_CONVERTER_MAX_FLOAT)
        for field in [capa_str + "_min", capa_str + "_max"]:
            df.loc[fixed, field] = capacity
        df.loc[fixed, "capital_cost"] = 0.0

    def _carrier_attribute_by_scenario(self, carrier_series: pd.Series, attribute: str) -> pd.Series:
        """Return the carrier attribute for each component row, preserving per-scenario variation."""
        carriers = self.pypsa_network.carriers
        if attribute not in carriers.columns:
            return pd.Series(0.0, index=carrier_series.index)
        if isinstance(carriers.index, pd.MultiIndex):
            col = carriers[attribute]
            scenarios = carrier_series.index.get_level_values(0)
            values = [col.get((s, c), 0.0) for s, c in zip(scenarios, carrier_series)]
            return pd.Series(values, index=carrier_series.index, dtype=float).fillna(0.0)
        attr_map: dict[str, float] = carriers[attribute].to_dict()
        return carrier_series.map(attr_map).astype(float).fillna(0.0)

    def _preprocess_pypsa_component(self, component_type: str, attribute_name: str | None = None) -> None:
        """Normalize carriers, rename, and optionally compute the emission factor and fix capacity.

        The emission factor (the carrier attribute of the primary_energy global constraints, co2_emissions by
        default) is added only for types listed in _EMISSION_FACTOR_COMPONENTS.
        attribute_name controls capacity fixing: pass None to skip (e.g. loads).
        """
        df = getattr(self.pypsa_network, component_type)
        if len(df) == 0:
            return
        carrier_series = df["carrier"].apply(_carrier_scalar)
        carrier_series = carrier_series.where(carrier_series != "", "null")
        df["carrier"] = carrier_series

        if component_type in _EMISSION_FACTOR_COMPONENTS:
            attribute = primary_energy_attribute(self.pypsa_network)
            df["co2_emissions"] = self._carrier_attribute_by_scenario(carrier_series, attribute)

        self._rename_pypsa_component(component_type)

        if attribute_name is not None:
            self._fix_capacity_non_extendable_attribute(component_type, attribute_name)

    def _prepare_multiport(self, component_type: str) -> None:
        """Columns of the link_multiport model: rate0 and efficiency..efficiency4, bus2..bus4.

        Link: rate0 = -1 (bus0 withdraws p0), efficiency(N) for bus1..bus4.
        Process: rate0 = rate0, efficiency(N) = rate1..rate4.
        A missing bus is "" (no connection) and its coefficient is 0.
        """
        component = getattr(self.pypsa_network.components, component_type)
        df = component.static
        if len(df) == 0:
            return
        if component_type == "links":
            df["rate0"] = -1.0
            coefficient_of = {i: ("efficiency" if i == 1 else f"efficiency{i}") for i in range(1, MAX_MULTIPORT_BUSES)}
        else:
            coefficient_of = {i: f"rate{i}" for i in range(1, MAX_MULTIPORT_BUSES)}
        for i in range(1, MAX_MULTIPORT_BUSES):
            bus_col = f"bus{i}"
            source = coefficient_of[i]
            target = "efficiency" if i == 1 else f"efficiency{i}"
            if bus_col not in df.columns:
                df[bus_col] = ""
            df[bus_col] = df[bus_col].fillna("").astype(str)
            if source not in df.columns:
                df[target] = 0.0
            elif source != target:
                df[target] = df[source]
                dynamic = component.dynamic.get(source)
                if dynamic is not None and not dynamic.empty:
                    component.dynamic[target] = dynamic.copy()
            df.loc[df[bus_col] == "", target] = 0.0

    def _prepare_ramp_limits(self, component_type: str) -> None:
        """Parameters of the *_ramp models, and the has_ramp_limit column used to route components to them.

        PyPSA constrains the ramp where ramp_limit_up / ramp_limit_down is set (not NaN), at every snapshot but
        the first, and at the first one only when p_init is set. The *_active flags carry where limits are set,
        and the limits are set to 1 elsewhere so the GEMS constraint, multiplied by 0, is always satisfied.
        """
        component = getattr(self.pypsa_network.components, component_type)
        df = component.static
        if len(df) == 0:
            return
        has_ramp = pd.Series(False, index=df.index)
        for attr in ("ramp_limit_up", "ramp_limit_down"):
            flag = attr.replace("ramp_limit_", "ramp_") + "_active"
            if attr not in df.columns:
                df[attr] = np.nan
            has_ramp |= df[attr].notna()
            dynamic = component.dynamic.get(attr)
            if dynamic is not None and not dynamic.empty:
                varying = dynamic.notna().any(axis=0)
                has_ramp |= pd.Series(df.index.isin(dynamic.columns[varying.to_numpy()]), index=df.index)
                dense = self.pypsa_network.get_switchable_as_dense(RAMP_COMPONENTS[component_type], attr)
                write_component_frame(self.pypsa_network, component_type, flag, dense.notna().astype(float))
                write_component_frame(self.pypsa_network, component_type, attr, dense.fillna(1.0))
            else:
                df[flag] = df[attr].notna().astype(float)
                df[attr] = df[attr].fillna(1.0)
        if "p_init" not in df.columns:
            df["p_init"] = np.nan
        df["p_init_active"] = df["p_init"].notna().astype(float)
        df["p_init"] = df["p_init"].fillna(0.0)
        df["has_ramp_limit"] = has_ramp.to_numpy()

    def _buses_with_ac_branches(self) -> set[str]:
        """Bus names that are endpoints of at least one Line or Transformer (angle-coupled)."""
        buses: set[str] = set()
        for component_type in ("lines", "transformers"):
            df = getattr(self.pypsa_network, component_type)
            if len(df) == 0:
                continue
            buses.update(df["bus0"].astype(str))
            buses.update(df["bus1"].astype(str))
        return buses

    def _add_bus_theta_bounds(self) -> None:
        """Add theta angle bounds to buses for DC LOPF. Fix one reference angle to 0 per AC island.

        An AC island is a connected component of the Line/Transformer graph. Its reference bus is a bus the user
        marked as Slack, else its first bus. Buses outside any island (Links-only, non-AC carriers, etc.) never
        enter the angle constraint matrix and keep free theta: FX bounds on those unused theta variables make
        Antares-Xpansion's Clp MPS reader fail with "No match for column ...theta...".

        The islands are computed directly rather than with n.determine_network_topology(), which builds a
        sub-network per bus and takes minutes on sector-coupled PyPSA-Eur networks.
        """
        buses_df = self.pypsa_network.components.buses.static
        if len(buses_df) == 0:
            return

        buses_df["theta_min"] = float("-inf")
        buses_df["theta_max"] = float("inf")

        ac_branch_buses: set[str] = self._buses_with_ac_branches()
        if not ac_branch_buses:
            return

        index = buses_df.index
        names = index.get_level_values(-1) if isinstance(index, pd.MultiIndex) else index
        bus_order = [str(name) for name in dict.fromkeys(names) if str(name) in ac_branch_buses]
        position = {bus: i for i, bus in enumerate(bus_order)}
        rows, cols = [], []
        for component_type in ("lines", "transformers"):
            df = getattr(self.pypsa_network, component_type)
            if len(df) == 0:
                continue
            rows += [position[str(bus)] for bus in df["bus0"]]
            cols += [position[str(bus)] for bus in df["bus1"]]
        graph = coo_matrix((np.ones(len(rows)), (rows, cols)), shape=(len(bus_order), len(bus_order)))
        _, island_of = connected_components(graph, directed=False)

        control = buses_df["control"].groupby(names, sort=False).first()
        slack_of_island: dict[int, str] = {}
        for bus, island in zip(bus_order, island_of):
            if island not in slack_of_island or (
                control.get(bus) == "Slack" and control.get(slack_of_island[island]) != "Slack"
            ):
                slack_of_island[island] = bus

        slack_mask = names.isin(list(slack_of_island.values()))
        buses_df.loc[slack_mask, "theta_min"] = 0.0
        buses_df.loc[slack_mask, "theta_max"] = 0.0

    def _add_modular_flag(self, component_type: str) -> None:
        """Compute modular expansion flag and ensure s_nom_mod is positive."""
        df = getattr(self.pypsa_network, component_type)
        if len(df) == 0:
            return
        df["modular"] = (df["s_nom_extendable"] & (df["s_nom_mod"] > 0)).astype(float)
        df.loc[df["s_nom_mod"] == 0, "s_nom_mod"] = 1.0

    def _preprocess_pypsa_components(self) -> None:
        self._preprocess_pypsa_component("loads")
        self._preprocess_pypsa_component("generators", "p_nom")
        self._preprocess_pypsa_component("stores", "e_nom")
        self._preprocess_pypsa_component("storage_units", "p_nom")
        self._preprocess_pypsa_component("links", "p_nom")
        self._preprocess_pypsa_component("processes", "p_nom")
        self._prepare_multiport("links")
        self._prepare_multiport("processes")
        for component_type in RAMP_COMPONENTS:
            self._prepare_ramp_limits(component_type)
        self.shared_params = add_time_weights(self.pypsa_network)
        add_standing_efficiency(self.pypsa_network, "stores")
        add_standing_efficiency(self.pypsa_network, "storage_units")
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
        if logger.isEnabledFor(logging.DEBUG):
            logger.debug("Preprocessed component types: %s", ", ".join(self.pypsa_components))
