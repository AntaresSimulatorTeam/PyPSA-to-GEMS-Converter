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
"""Registration of the preprocessed PyPSA components onto GEMS models.

A PyPSA component type may map onto several GEMS models (see the header of pypsa_models.yml): each component
is routed to one model, and each (component type, model) pair is registered with the data of its components.
"""

import re
from typing import Any, cast

import numpy as np
import pandas as pd
from pypsa import Network

from src.converter_limitations import MAX_MULTIPORT_BUSES, SUPPORTED_GLOBAL_CONSTRAINT_TYPES
from src.models.pypsa_model_schema import PyPSAComponentData, PyPSAGlobalConstraintData
from src.pypsa_preprocessor import NOMINAL_ATTRS
from src.time_granularity import time_weight_series
from src.utils import dynamic_dict_pypsa_to_polars, static_pypsa_to_polars

LAST_TIME_STEP_INDICATOR = "last_time_step_indicator"

_EMISSION = {"co2_emissions": "emission_factor"}
_TIME_WEIGHTS = {"hours_per_time_step": "hours_per_time_step", "objective_weighting": "objective_weighting"}
_RAMP = {
    "ramp_limit_up": "ramp_limit_up",
    "ramp_limit_down": "ramp_limit_down",
    "ramp_up_active": "ramp_up_active",
    "ramp_down_active": "ramp_down_active",
    "p_init": "p_init",
    "p_init_active": "p_init_active",
}

_GENERATOR_PARAMS: dict[str, str] = {
    "p_nom_min": "p_nom_min",
    "p_nom_max": "p_nom_max",
    "p_min_pu": "p_min_pu",
    "p_max_pu": "p_max_pu",
    "marginal_cost": "marginal_cost",
    "capital_cost": "capital_cost",
    "e_sum_min": "e_sum_min",
    "e_sum_max": "e_sum_max",
    "sign": "sign",
    "efficiency": "efficiency",
    **_EMISSION,
    **_TIME_WEIGHTS,
}

_LINK_PARAMS: dict[str, str] = {
    "efficiency": "efficiency",
    "p_nom_min": "p_nom_min",
    "p_nom_max": "p_nom_max",
    "p_min_pu": "p_min_pu",
    "p_max_pu": "p_max_pu",
    "marginal_cost": "marginal_cost",
    "capital_cost": "capital_cost",
    "length": "length",
    "objective_weighting": "objective_weighting",
}

_MULTIPORT_PARAMS: dict[str, str] = {
    "rate0": "rate0",
    **_LINK_PARAMS,
    **{f"efficiency{i}": f"efficiency{i}" for i in range(2, MAX_MULTIPORT_BUSES)},
}

_STORAGE_UNIT_PARAMS: dict[str, str] = {
    "p_nom_min": "p_nom_min",
    "p_nom_max": "p_nom_max",
    "p_min_pu": "p_min_pu",
    "p_max_pu": "p_max_pu",
    "sign": "sign",
    "efficiency_store": "efficiency_store",
    "efficiency_dispatch": "efficiency_dispatch",
    "standing_efficiency": "standing_efficiency",
    "max_hours": "max_hours",
    "marginal_cost": "marginal_cost",
    "capital_cost": "capital_cost",
    "marginal_cost_storage": "marginal_cost_storage",
    "spill_cost": "spill_cost",
    "inflow": "inflow",
    **_EMISSION,
    **_TIME_WEIGHTS,
}

_STORE_PARAMS: dict[str, str] = {
    "sign": "sign",
    "e_nom_min": "e_nom_min",
    "e_nom_max": "e_nom_max",
    "e_min_pu": "e_min_pu",
    "e_max_pu": "e_max_pu",
    "standing_efficiency": "standing_efficiency",
    "marginal_cost": "marginal_cost",
    "capital_cost": "capital_cost",
    "marginal_cost_storage": "marginal_cost_storage",
    **_EMISSION,
    **_TIME_WEIGHTS,
}

_ONE_PORT = {"bus": ("p_balance_port", "p_balance_port")}
_TWO_PORT_LINK = {"bus0": ("p0_port", "p_balance_port"), "bus1": ("p1_port", "p_balance_port")}
_MULTIPORT_LINK = {f"bus{i}": (f"p{i}_port", "p_balance_port") for i in range(MAX_MULTIPORT_BUSES)}
_BRANCH = {"bus0": ("bus0_p_port", "p_balance_port"), "bus1": ("bus1_p_port", "p_balance_port")}

# Lines and transformers; their *_modular variants add s_nom_mod.
_BRANCH_PARAMS: dict[str, str] = {
    "s_nom_min": "s_nom_min",
    "s_nom_max": "s_nom_max",
    "s_max_pu": "s_max_pu",
    "capital_cost": "capital_cost",
}
_LINE_PARAMS: dict[str, str] = {"x_pu": "x", **_BRANCH_PARAMS, "length": "length"}
_TRANSFORMER_PARAMS: dict[str, str] = {"x_pu_eff": "x_pu_eff", **_BRANCH_PARAMS}

_SENSE_SUFFIX = {"<=": "max", "==": "eq", ">=": "min"}


def _names(index: pd.Index) -> pd.Index:
    return index.get_level_values(-1) if isinstance(index, pd.MultiIndex) else index


def _per_name(static: pd.DataFrame, column: str) -> pd.Series:
    """One value per component name: the column must not vary between scenarios."""
    grouped = static[column].groupby(_names(static.index), sort=False)
    counts = grouped.nunique(dropna=False)
    if (counts > 1).any():
        raise ValueError(
            f"Converter needs '{column}' to be the same in every scenario, but it varies for: "
            f"{', '.join(map(str, counts.index[counts > 1][:5]))}"
        )
    return grouped.first()


def _first_scenario(static: pd.DataFrame) -> pd.DataFrame:
    """Static data of the first scenario, indexed by component name."""
    if isinstance(static.index, pd.MultiIndex) and len(static) > 0:
        first = static.index.get_level_values(0)[0]
        return cast(pd.DataFrame, static.xs(first, level=0))
    return static


def _subset(static: pd.DataFrame, dynamic: Any, names: pd.Index) -> tuple[pd.DataFrame, dict[str, pd.DataFrame]]:
    """Static rows and dynamic columns of the given component names."""
    static_subset = static[_names(static.index).isin(names)]
    dynamic_subset: dict[str, pd.DataFrame] = {}
    for key in dynamic.keys():
        df = dynamic[key]
        dynamic_subset[key] = df.loc[:, _names(df.columns).isin(names)] if len(df.columns) else df
    return static_subset, dynamic_subset


def _parse_carriers(carrier_attribute: str) -> list[str]:
    """Carrier list of a transmission expansion limit, e.g. 'AC, DC' or '[AC, DC]'."""
    cleaned = re.sub(r"[\[\]\(\)]", "", str(carrier_attribute))
    return [s.strip() for s in cleaned.split(",") if s.strip()]


class PyPSARegister:
    def __init__(self, pypsa_network: Network, shared_params: dict[str, dict[str, str]] | None = None):
        self.pypsa_network = pypsa_network
        self.pypsa_components_data: dict[str, PyPSAComponentData] = {}
        self.pypsa_globalconstraints_data: dict[str, PyPSAGlobalConstraintData] = {}
        # Shared data series (one file for every component referencing it): {key: values over time steps}
        self.shared_series: dict[str, list[float]] = {}
        self._shared_params = shared_params or {}
        # GEMS model of each component, keyed by GEMS component id
        self.component_models: dict[str, str] = {}

    def register(self) -> tuple[dict[str, PyPSAComponentData], dict[str, PyPSAGlobalConstraintData]]:
        self.shared_series.update(time_weight_series(self.pypsa_network))

        self._register_generators()
        self._register("loads", "load", {"p_set": "p_set", "q_set": "q_set", "sign": "sign"}, _ONE_PORT)
        self._register(
            "buses",
            "bus",
            {
                "v_nom": "v_nom",
                "x": "x",
                "y": "y",
                "v_mag_pu_set": "v_mag_pu_set",
                "v_mag_pu_min": "v_mag_pu_min",
                "v_mag_pu_max": "v_mag_pu_max",
                "theta_min": "theta_min",
                "theta_max": "theta_max",
            },
            {},
        )
        self._register_links("links")
        self._register_links("processes")
        self._register_storage(
            "storage_units",
            "cyclic_state_of_charge",
            "storage_unit",
            _STORAGE_UNIT_PARAMS,
            {"state_of_charge_initial": "state_of_charge_initial"},
        )
        self._register_storage("stores", "e_cyclic", "store", _STORE_PARAMS, {"e_initial": "e_initial"})
        self._register_branches("lines", "line", _LINE_PARAMS)
        self._register_branches("transformers", "transformer", _TRANSFORMER_PARAMS)

        self._register_pypsa_globalconstraints()
        self._register_bus_carrier_limits()

        return self.pypsa_components_data, self.pypsa_globalconstraints_data

    # ------------------------------------------------------------------ components

    def _register(
        self,
        component_type: str,
        gems_model_id: str,
        params: dict[str, str],
        connections: dict[str, tuple[str, str]],
        names: pd.Index | None = None,
        extra_shared: dict[str, str] | None = None,
    ) -> None:
        """Register the components of a type (or the given subset of them) onto one GEMS model.

        A parameter that is a shared data series (time-varying snapshot weighting, last-time-step indicator) is
        removed from the per-component parameters and referenced through shared_params instead.
        """
        component = getattr(self.pypsa_network.components, component_type)
        static = component.static
        if len(static) == 0:
            return
        if names is None:
            names = pd.Index(_names(static.index).unique())
        if len(names) == 0:
            return
        model_params = set(params.values())
        shared = {
            param: key
            for param, key in {**self._shared_params.get(component_type, {}), **(extra_shared or {})}.items()
            if param in model_params or param in (extra_shared or {})
        }
        gems_params = {pypsa: gems for pypsa, gems in params.items() if gems not in shared}
        static_subset, dynamic_subset = _subset(static, component.dynamic, names)
        for name in names:
            self.component_models[str(name)] = gems_model_id
        self._register_pypsa_component(
            f"{component_type}:{gems_model_id}",
            static_subset,
            dynamic_subset,
            gems_model_id,
            gems_params,
            connections,
            shared,
        )

    def _register_pypsa_component(
        self,
        pypsa_model_id: str,
        constant_data: pd.DataFrame,
        time_dependent_data: dict[str, pd.DataFrame],
        gems_model_id: str,
        pypsa_params_to_gems_params: dict[str, str],
        pypsa_params_to_gems_connections: dict[str, tuple[str, str]],
        shared_params: dict[str, str] | None = None,
    ) -> None:
        if pypsa_model_id in self.pypsa_components_data:
            raise ValueError(f"{pypsa_model_id} already registered !")

        # Convert PyPSA pandas DataFrames to Polars for internal use
        constant_data_pl = static_pypsa_to_polars(constant_data)
        time_dependent_data_pl = dynamic_dict_pypsa_to_polars(time_dependent_data)

        self.pypsa_components_data[pypsa_model_id] = PyPSAComponentData(
            pypsa_model_id,
            constant_data_pl,
            time_dependent_data_pl,
            gems_model_id,
            pypsa_params_to_gems_params,
            pypsa_params_to_gems_connections,
            shared_params or {},
        )

    def _all_names(self, component_type: str) -> pd.Index:
        static: pd.DataFrame = getattr(self.pypsa_network.components, component_type).static
        return cast(pd.Index, pd.Index(_names(static.index).unique()))

    def _flagged_names(self, component_type: str, mask: pd.Series) -> pd.Index:
        """Names for which the per-row mask holds in at least one scenario."""
        static: pd.DataFrame = getattr(self.pypsa_network.components, component_type).static
        flagged = mask.groupby(_names(static.index), sort=False).any()
        return cast(pd.Index, pd.Index(flagged.index[flagged.to_numpy()]))

    def _ramp_names(self, component_type: str) -> pd.Index:
        static: pd.DataFrame = getattr(self.pypsa_network.components, component_type).static
        if len(static) == 0 or "has_ramp_limit" not in static.columns:
            return pd.Index([])
        return self._flagged_names(component_type, static["has_ramp_limit"].astype(bool))

    def _register_generators(self) -> None:
        names = self._all_names("generators")
        ramp = self._ramp_names("generators")
        self._register("generators", "generator", _GENERATOR_PARAMS, _ONE_PORT, names.difference(ramp, sort=False))
        self._register("generators", "generator_ramp", {**_GENERATOR_PARAMS, **_RAMP}, _ONE_PORT, ramp)

    def _multiport_names(self, component_type: str) -> pd.Index:
        if component_type == "processes":
            return self._all_names(component_type)
        static: pd.DataFrame = getattr(self.pypsa_network.components, component_type).static
        mask = pd.Series(False, index=static.index)
        for i in range(2, MAX_MULTIPORT_BUSES):
            col = f"bus{i}"
            if col in static.columns:
                mask |= static[col].fillna("").astype(str).str.strip() != ""
        return self._flagged_names(component_type, mask)

    def _register_links(self, component_type: str) -> None:
        if len(getattr(self.pypsa_network, component_type)) == 0:
            return
        names = self._all_names(component_type)
        ramp = self._ramp_names(component_type)
        multiport = self._multiport_names(component_type)
        two_port = names.difference(multiport, sort=False)
        self._register(component_type, "link", _LINK_PARAMS, _TWO_PORT_LINK, two_port.difference(ramp, sort=False))
        self._register(
            component_type, "link_ramp", {**_LINK_PARAMS, **_RAMP}, _TWO_PORT_LINK, two_port.intersection(ramp)
        )
        self._register(
            component_type,
            "link_multiport",
            _MULTIPORT_PARAMS,
            _MULTIPORT_LINK,
            multiport.difference(ramp, sort=False),
        )
        self._register(
            component_type,
            "link_multiport_ramp",
            {**_MULTIPORT_PARAMS, **_RAMP},
            _MULTIPORT_LINK,
            multiport.intersection(ramp),
        )

    def _register_branches(self, component_type: str, gems_model_id: str, params: dict[str, str]) -> None:
        """Lines or transformers: modular expansion (integer module count) goes to the ``*_modular`` variant so that
        studies without it stay linear programs."""
        static: pd.DataFrame = getattr(self.pypsa_network.components, component_type).static
        if len(static) == 0:
            return
        names = self._all_names(component_type)
        modular = self._flagged_names(component_type, static["modular"].astype(bool))
        self._register(component_type, gems_model_id, params, _BRANCH, names.difference(modular, sort=False))
        self._register(
            component_type, f"{gems_model_id}_modular", {**params, "s_nom_mod": "s_nom_mod"}, _BRANCH, modular
        )

    def _register_storage(
        self,
        component_type: str,
        cyclic_column: str,
        gems_model_id: str,
        params: dict[str, str],
        non_cyclic_params: dict[str, str],
    ) -> None:
        static: pd.DataFrame = getattr(self.pypsa_network.components, component_type).static
        if len(static) == 0:
            return
        cyclic = _per_name(static, cyclic_column).astype(bool)
        cyclic_names = pd.Index(cyclic.index[cyclic.to_numpy()])
        non_cyclic_names = pd.Index(cyclic.index[~cyclic.to_numpy()])
        self._register(component_type, gems_model_id, params, _ONE_PORT, cyclic_names)
        if len(non_cyclic_names) == 0:
            return
        n_steps = len(self.pypsa_network.snapshots)
        self.shared_series[LAST_TIME_STEP_INDICATOR] = [0.0] * (n_steps - 1) + [1.0]
        self._register(
            component_type,
            f"{gems_model_id}_non_cyclic",
            {**params, **non_cyclic_params},
            _ONE_PORT,
            non_cyclic_names,
            extra_shared={"is_last_time_step": LAST_TIME_STEP_INDICATOR},
        )

    # ------------------------------------------------------------------ global constraints

    def _static(self, component_type: str) -> pd.DataFrame:
        return _first_scenario(getattr(self.pypsa_network.components, component_type).static)

    def _carriers_with_attribute(self, attribute: str) -> pd.Index:
        """Carriers whose attribute is non-zero in at least one scenario."""
        carriers: pd.DataFrame = self.pypsa_network.carriers
        if attribute not in carriers.columns:
            return pd.Index([])
        values = carriers[attribute].astype(float).fillna(0.0).abs()
        grouped = values.groupby(_names(carriers.index)).max()
        return cast(pd.Index, pd.Index(grouped.index[grouped.to_numpy() != 0]))

    def _connect(self, names: pd.Index, port: str) -> list[tuple[str, str]]:
        return [(str(name), port) for name in names if str(name) in self.component_models]

    def _non_cyclic_storage(self) -> dict[str, pd.DataFrame]:
        """Static data (first scenario) of the non-cyclic stores and storage units, keyed by component type."""
        storage = {}
        for component_type, model in (("stores", "store_non_cyclic"), ("storage_units", "storage_unit_non_cyclic")):
            df = self._static(component_type)
            if len(df) > 0:
                storage[component_type] = df[[self.component_models.get(str(name)) == model for name in df.index]]
        return storage

    def _primary_energy_contributors(self, attribute: str) -> list[tuple[str, str]]:
        emitting = self._carriers_with_attribute(attribute)
        contributors: list[tuple[str, str]] = []
        gens = self._static("generators")
        if len(gens) > 0:
            contributors += self._connect(pd.Index(gens.index[gens["carrier"].isin(emitting)]), "emission_port")
        for df in self._non_cyclic_storage().values():
            contributors += self._connect(pd.Index(df.index[df["carrier"].isin(emitting)]), "emission_port")
        return contributors

    def _operational_limit_contributors(self, carrier: str) -> list[tuple[str, str]]:
        contributors: list[tuple[str, str]] = []
        gens = self._static("generators")
        if len(gens) > 0:
            contributors += self._connect(pd.Index(gens.index[gens["carrier"] == carrier]), "operational_port")
        for df in self._non_cyclic_storage().values():
            contributors += self._connect(pd.Index(df.index[df["carrier"] == carrier]), "operational_port")
        return contributors

    def _co2_atmosphere_contributors(self, attribute: str) -> list[tuple[str, str]]:
        """PyPSA-Eur: non-cyclic stores whose bus carrier has a non-zero carrier attribute."""
        stores = self._non_cyclic_storage().get("stores")
        if stores is None or len(stores) == 0:
            return []
        buses = self._static("buses")
        emitting = self._carriers_with_attribute(attribute)
        bus_carrier = stores["bus"].map(buses["carrier"])
        return self._connect(pd.Index(stores.index[bus_carrier.isin(emitting).to_numpy()]), "final_level_port")

    def _extendable(self, component_type: str) -> pd.DataFrame:
        df = self._static(component_type)
        if len(df) == 0:
            return df
        return df[df[f"{NOMINAL_ATTRS[component_type]}_extendable"].astype(bool)]

    def _transmission_contributors(self, carriers: list[str], port: str) -> list[tuple[str, str]]:
        contributors: list[tuple[str, str]] = []
        for component_type in ("lines", "links"):
            df = self._extendable(component_type)
            if len(df) > 0 and "carrier" in df.columns:
                contributors += self._connect(pd.Index(df.index[df["carrier"].isin(carriers)]), port)
        return contributors

    def _tech_capacity_contributors(self, carrier: str, bus: str | None) -> list[tuple[str, str]]:
        contributors: list[tuple[str, str]] = []
        for component_type in NOMINAL_ATTRS:
            df = self._extendable(component_type)
            if len(df) == 0 or "carrier" not in df.columns:
                continue
            mask = df["carrier"] == carrier
            if bus is not None:
                mask &= df["bus" if "bus" in df.columns else "bus0"] == bus
            contributors += self._connect(pd.Index(df.index[mask.to_numpy()]), "capacity_port")
        return contributors

    def _add_global_constraint(
        self,
        component_id: str,
        family: str,
        sense: str,
        quota_by_scenario: dict[str, float],
        contributors: list[tuple[str, str]],
        pypsa_type: str,
    ) -> None:
        port = {"co2": "emission_port", "quantity": "quantity_port", "capacity": "capacity_port"}[family]
        self.pypsa_globalconstraints_data[component_id] = PyPSAGlobalConstraintData(
            gems_component_id=component_id,
            gems_model_id=f"global_constraint_{family}_{_SENSE_SUFFIX[sense]}",
            gems_port_id=port,
            quota_by_scenario=quota_by_scenario,
            gems_components_and_ports=contributors,
            pypsa_type=pypsa_type,
        )

    def _scenarios_of(self, df: pd.DataFrame) -> list[str]:
        if isinstance(df.index, pd.MultiIndex):
            return [str(s) for s in df.index.get_level_values(0)]
        return ["default"]

    def _register_pypsa_globalconstraints(self) -> None:
        glcs = self.pypsa_network.global_constraints
        if len(glcs) == 0:
            return
        names = _names(glcs.index)
        for name in names.unique():
            rows = glcs[names == name]
            glc = rows.iloc[0]
            constraint_type = str(glc["type"])
            if constraint_type not in SUPPORTED_GLOBAL_CONSTRAINT_TYPES:
                continue  # Ignored with a warning by check_converter_limitations, as PyPSA ignores it
            sense = str(glc["sense"])
            attribute = str(glc["carrier_attribute"])
            quota = dict(zip(self._scenarios_of(rows), rows["constant"].astype(float)))
            family = "capacity"
            if constraint_type == "primary_energy":
                family, contributors = "co2", self._primary_energy_contributors(attribute)
            elif constraint_type == "operational_limit":
                family, contributors = "quantity", self._operational_limit_contributors(attribute)
            elif constraint_type == "co2_atmosphere":
                family, contributors = "quantity", self._co2_atmosphere_contributors(attribute)
            elif constraint_type == "transmission_volume_expansion_limit":
                contributors = self._transmission_contributors(_parse_carriers(attribute), "expansion_volume_port")
            elif constraint_type == "transmission_expansion_cost_limit":
                contributors = self._transmission_contributors(_parse_carriers(attribute), "expansion_cost_port")
            else:  # tech_capacity_expansion_limit
                bus = glc.get("bus")
                bus_name = str(bus) if isinstance(bus, str) and bus != "" else None
                contributors = self._tech_capacity_contributors(attribute, bus_name)
            if not contributors:
                # PyPSA adds no constraint when no variable enters it: neither does the converter.
                continue
            component_id = f"global_constraint_{str(name).replace(' ', '_')}"
            self._add_global_constraint(component_id, family, sense, quota, contributors, constraint_type)

    def _register_bus_carrier_limits(self) -> None:
        """Bus columns nom_min_<carrier> / nom_max_<carrier> (deprecated in PyPSA 1.0): one capacity constraint per
        bus and column, on the extendable one-port components of that carrier at that bus."""
        buses = self.pypsa_network.components.buses.static
        columns = [col for col in buses.columns if str(col).startswith(("nom_min_", "nom_max_"))]
        for col in columns:
            sense = ">=" if col.startswith("nom_min_") else "<="
            carrier = col[len("nom_max_") :]
            for bus in _names(buses.index).unique():
                rows = buses[_names(buses.index) == bus]
                values = rows[col].astype(float)
                if values.isna().all():
                    continue
                contributors: list[tuple[str, str]] = []
                for component_type in ("generators", "storage_units", "stores"):
                    df = self._extendable(component_type)
                    if len(df) > 0:
                        mask = (df["carrier"] == carrier) & (df["bus"] == bus)
                        contributors += self._connect(pd.Index(df.index[mask.to_numpy()]), "capacity_port")
                if not contributors:
                    continue
                quota = dict(zip(self._scenarios_of(rows), np.nan_to_num(values.to_numpy())))
                component_id = f"global_constraint_bus_{bus}_{col}".replace(" ", "_")
                self._add_global_constraint(component_id, "capacity", sense, quota, contributors, "bus_" + col)
