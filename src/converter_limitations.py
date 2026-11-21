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
"""Converter limitations: the PyPSA features the converter doesn't model (see COMPATIBILITY.md).

check_converter_limitations() runs in the PyPSAStudyConverter constructor, before any file is written.
A feature that changes the optimum raises ValueError (all findings in one message), so a converted study never
silently gives different results from PyPSA. A feature that doesn't, but that the user should know about,
logs a warning. Each check compares against PyPSA's default value, so a network that doesn't use the feature
passes.

Piecewise curves (c.piecewise) need no check: the converter gives every network a "default" scenario, and
PyPSA refuses to set scenarios on a network with piecewise data (NotImplementedError, PyPSA 1.4.0).
"""

import logging
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

import numpy as np
from pypsa import Network

from src.time_granularity import resolve_time_weights

# Child of the converter's logger (CONVERTER_LOGGER_NAME in src/pypsa_converter.py), so its handlers get the warnings.
logger = logging.getLogger("pypsa_to_gems_converter.converter_limitations")

# Number of offending component names quoted in a message.
_MAX_NAMES = 5

_LABELS = {
    "loads": "Loads",
    "generators": "Generators",
    "links": "Links",
    "storage_units": "Storage Units",
    "stores": "Stores",
    "lines": "Lines",
    "transformers": "Transformers",
}

_EXTENDABLE_FLAGS = {
    "generators": "p_nom_extendable",
    "links": "p_nom_extendable",
    "storage_units": "p_nom_extendable",
    "stores": "e_nom_extendable",
    "lines": "s_nom_extendable",
    "transformers": "s_nom_extendable",
}


def _is_set(values: Any) -> Any:
    """Default NaN."""
    return values.notna()


def _is_finite(values: Any) -> Any:
    """Default NaN or ±inf."""
    return np.isfinite(values.astype(float))


def _is_nonzero(values: Any) -> Any:
    """Default 0."""
    return values.fillna(0) != 0


def _is_true(values: Any) -> Any:
    """Default False."""
    return values.fillna(False).astype(bool)


def _is_false(values: Any) -> Any:
    """Required True."""
    return ~values.fillna(True).astype(bool)


def _is_not_one(values: Any) -> Any:
    """Required 1."""
    return values != 1


@dataclass(frozen=True)
class _AttributeCheck:
    component_types: tuple[str, ...]
    attr: str
    detected: Callable[[Any], Any]
    reason: str
    # PyPSA only uses the attribute for extendable components (capital cost, modular expansion).
    extendable_only: bool = False


_CAPACITY_COMPONENTS = ("generators", "links", "storage_units", "stores", "lines", "transformers")

# Features that change the optimum: ValueError.
_ERROR_CHECKS: tuple[_AttributeCheck, ...] = (
    _AttributeCheck(
        ("generators", "storage_units", "stores"),
        "marginal_cost_quadratic",
        _is_nonzero,
        "quadratic costs are not modelled",
    ),
    _AttributeCheck(("generators",), "committable", _is_true, "unit commitment is not modelled"),
    _AttributeCheck(
        ("generators", "loads", "links", "lines", "transformers", "storage_units", "stores"),
        "active",
        _is_false,
        "inactive components are not dropped",
    ),
    _AttributeCheck(("storage_units", "stores"), "sign", _is_not_one, "only sign = 1 is modelled"),
    _AttributeCheck(("storage_units",), "cyclic_state_of_charge", _is_false, "only cyclic state of charge is modelled"),
    _AttributeCheck(("stores",), "e_cyclic", _is_false, "only cyclic energy level is modelled"),
    _AttributeCheck(("transformers",), "phase_shift", _is_nonzero, "phase shifts change the KVL flows"),
    _AttributeCheck(("lines", "transformers"), "v_ang_max", _is_finite, "the angle difference limit caps flows"),
    _AttributeCheck(
        ("generators", "links", "storage_units"), "p_nom_set", _is_set, "the capacity is fixed in PyPSA only"
    ),
    _AttributeCheck(("stores",), "e_nom_set", _is_set, "the capacity is fixed in PyPSA only"),
    _AttributeCheck(("lines", "transformers"), "s_nom_set", _is_set, "the capacity is fixed in PyPSA only"),
    _AttributeCheck(("links",), "delay", _is_nonzero, "delayed delivery is not modelled"),
    _AttributeCheck(("generators", "links"), "maintainable", _is_true, "maintenance outages are not modelled"),
    _AttributeCheck(("generators", "links"), "ramp_limit_up", _is_finite, "ramp constraints are not modelled"),
    _AttributeCheck(("generators", "links"), "ramp_limit_down", _is_finite, "ramp constraints are not modelled"),
    _AttributeCheck(
        ("generators", "links", "storage_units"),
        "p_nom_mod",
        _is_nonzero,
        "modular expansion is not modelled",
        extendable_only=True,
    ),
    _AttributeCheck(("stores",), "e_nom_mod", _is_nonzero, "modular expansion is not modelled", extendable_only=True),
    _AttributeCheck(_CAPACITY_COMPONENTS, "fom_cost", _is_nonzero, "it changes the capital cost", extendable_only=True),
    _AttributeCheck(
        _CAPACITY_COMPONENTS, "overnight_cost", _is_set, "it changes the capital cost", extendable_only=True
    ),
    _AttributeCheck(("storage_units",), "state_of_charge_set", _is_set, "fixed operation is not modelled"),
    _AttributeCheck(("storage_units",), "p_dispatch_set", _is_set, "fixed operation is not modelled"),
    _AttributeCheck(("storage_units",), "p_store_set", _is_set, "fixed operation is not modelled"),
    _AttributeCheck(("stores",), "e_set", _is_set, "fixed operation is not modelled"),
)

# Features that don't change the optimum on their own: warning.
_WARNING_CHECKS: tuple[_AttributeCheck, ...] = (
    _AttributeCheck(("lines", "transformers"), "v_ang_min", _is_finite, "deprecated and ignored by PyPSA >= 1.4.0"),
    _AttributeCheck(("generators", "links"), "p_init", _is_set, "only used by ramp limits, which are not supported"),
)


def _component_name(key: Any) -> str:
    """Component name from a static index entry or dynamic column (a tuple with scenarios)."""
    return str(key[-1] if isinstance(key, tuple) else key)


def _quote(names: set[str]) -> str:
    listed = sorted(names)
    suffix = f" and {len(listed) - _MAX_NAMES} more" if len(listed) > _MAX_NAMES else ""
    return ", ".join(listed[:_MAX_NAMES]) + suffix


def _offending_names(network: Network, component_type: str, check: _AttributeCheck) -> set[str]:
    """Names of the components where the attribute differs from its default, as a static value or a series."""
    c = getattr(network.components, component_type)
    names: set[str] = set()
    if len(c.static) > 0 and check.attr in c.static.columns:
        mask = np.asarray(check.detected(c.static[check.attr]), dtype=bool)
        if check.extendable_only:
            mask = mask & c.static[_EXTENDABLE_FLAGS[component_type]].to_numpy(dtype=bool)
        names.update(_component_name(key) for key in c.static.index[mask])
    if check.attr in c.dynamic.keys():
        series = c.dynamic[check.attr]
        if not series.empty:
            mask = np.asarray(check.detected(series).any(axis=0), dtype=bool)
            names.update(_component_name(key) for key in series.columns[mask])
    return names


def _attribute_findings(network: Network, checks: tuple[_AttributeCheck, ...]) -> list[str]:
    findings = []
    for check in checks:
        for component_type in check.component_types:
            names = _offending_names(network, component_type, check)
            if names:
                findings.append(f"{_LABELS[component_type]} with {check.attr} ({check.reason}): {_quote(names)}")
    return findings


def _network_findings(network: Network) -> list[str]:
    findings = []
    if len(network.investment_periods) > 0:
        findings.append(f"investment periods ({len(network.investment_periods)} periods)")
    try:
        resolve_time_weights(network)
    except ValueError as error:
        findings.append(str(error))

    global_constraints = network.global_constraints
    for name in global_constraints.index:
        constraint_type = global_constraints.loc[name, "type"]
        if constraint_type != "primary_energy":
            findings.append(
                f"GlobalConstraint '{_component_name(name)}' of type '{constraint_type}' (only 'primary_energy')"
            )
        carrier_attribute = global_constraints.loc[name, "carrier_attribute"]
        if carrier_attribute != "co2_emissions":
            findings.append(
                f"GlobalConstraint '{_component_name(name)}' on carrier attribute '{carrier_attribute}' "
                "(only 'co2_emissions')"
            )

    processes = network.components.processes.static
    if len(processes) > 0:
        findings.append(f"Process components (no GEMS model): {_quote({_component_name(k) for k in processes.index})}")

    links = network.components.links.static
    for col in links.columns:
        if col.startswith("bus") and col[3:].isdigit() and int(col[3:]) >= 2:
            mask = links[col].fillna("").astype(str).str.strip() != ""
            if mask.any():
                names = {_component_name(k) for k in links.index[mask.to_numpy()]}
                findings.append(f"Links with {col} (only bus0 -> bus1 is converted): {_quote(names)}")

    transformers = network.components.transformers.static
    if len(transformers) > 0:
        mask = transformers["phase_shift_min"] < transformers["phase_shift_max"]
        if mask.any():
            names = {_component_name(k) for k in transformers.index[mask.to_numpy()]}
            findings.append(
                f"Transformers with phase_shift_min < phase_shift_max (optimised phase shifts change the KVL flows): "
                f"{_quote(names)}"
            )

    buses = network.components.buses.static
    bus_carrier_cols = [col for col in buses.columns if col.startswith(("nom_min_", "nom_max_"))]
    if bus_carrier_cols and buses[bus_carrier_cols].notna().any().any():
        findings.append(f"Buses with {', '.join(bus_carrier_cols)} (capacity limits per bus and carrier)")

    if network.has_risk_preference:
        findings.append("a risk preference (CVaR objective)")
    return findings


def check_converter_limitations(network: Network) -> None:
    """Raise ValueError if the network uses a feature that changes the optimum, and warn about the others."""
    for finding in _attribute_findings(network, _WARNING_CHECKS):
        logger.warning("Converter ignores %s.", finding)

    errors = _network_findings(network) + _attribute_findings(network, _ERROR_CHECKS)
    if errors:
        raise ValueError(
            "Converter does not support these PyPSA features, so the GEMS study would give different results:\n"
            + "\n".join(f"- {error}" for error in errors)
        )
