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

drop_inactive_components() runs before it: PyPSA leaves inactive components out of the optimisation, and so does
the converter, so their attributes are never checked.

Piecewise curves (c.piecewise) need no check: the converter gives every network a "default" scenario, and
PyPSA refuses to set scenarios on a network with piecewise data (NotImplementedError, PyPSA 1.4.0).
"""

import logging
import re
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

import numpy as np
import pandas as pd
from pypsa import Network

from src.time_granularity import resolve_time_weights

# Child of the converter's logger (CONVERTER_LOGGER_NAME in src/pypsa_converter.py), so its handlers get the warnings.
logger = logging.getLogger("pypsa_to_gems_converter.converter_limitations")

# Number of offending component names quoted in a message.
_MAX_NAMES = 5

# Multi-port links and processes: bus0..bus4, as the link_multiport GEMS model (PyPSA-Eur uses up to bus4).
MAX_MULTIPORT_BUSES = 5

_LABELS = {
    "loads": "Loads",
    "generators": "Generators",
    "links": "Links",
    "processes": "Processes",
    "storage_units": "Storage Units",
    "stores": "Stores",
    "lines": "Lines",
    "transformers": "Transformers",
}

_CLASS_NAMES = {
    "loads": "Load",
    "generators": "Generator",
    "links": "Link",
    "processes": "Process",
    "storage_units": "StorageUnit",
    "stores": "Store",
    "lines": "Line",
    "transformers": "Transformer",
}

_EXTENDABLE_FLAGS = {
    "generators": "p_nom_extendable",
    "links": "p_nom_extendable",
    "processes": "p_nom_extendable",
    "storage_units": "p_nom_extendable",
    "stores": "e_nom_extendable",
    "lines": "s_nom_extendable",
    "transformers": "s_nom_extendable",
}

# Global constraint types the converter models, and the senses PyPSA accepts.
SUPPORTED_GLOBAL_CONSTRAINT_TYPES = frozenset(
    {
        "primary_energy",
        "operational_limit",
        "transmission_volume_expansion_limit",
        "transmission_expansion_cost_limit",
        "tech_capacity_expansion_limit",
        # Not a PyPSA type: PyPSA-Eur's CO2 limit on sector-coupled networks, enforced in its extra_functionality
        # (final level of the non-cyclic stores on emission buses). Plain n.optimize() ignores it.
        "co2_atmosphere",
    }
)
_SENSES = frozenset({"<=", "==", ">="})


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


@dataclass(frozen=True)
class _AttributeCheck:
    component_types: tuple[str, ...]
    attr: str
    detected: Callable[[Any], Any]
    reason: str
    # PyPSA only uses the attribute for extendable components (modular expansion).
    extendable_only: bool = False
    # PyPSA only fails on the attribute for non-extendable components (*_nom_set).
    fixed_only: bool = False


# Features that change the optimum: ValueError.
_ERROR_CHECKS: tuple[_AttributeCheck, ...] = (
    _AttributeCheck(
        ("generators", "links", "processes", "storage_units", "stores"),
        "marginal_cost_quadratic",
        _is_nonzero,
        "quadratic costs are non-linear, GEMS is restricted to linear problems",
    ),
    _AttributeCheck(
        ("generators", "links", "processes"), "committable", _is_true, "unit commitment is not modelled yet"
    ),
    _AttributeCheck(
        ("generators", "links", "processes"), "maintainable", _is_true, "maintenance outages are not modelled"
    ),
    _AttributeCheck(("transformers",), "phase_shift", _is_nonzero, "phase shifts change the KVL flows"),
    _AttributeCheck(("lines", "transformers"), "v_ang_max", _is_finite, "the angle difference limit caps flows"),
    _AttributeCheck(
        ("generators", "links", "processes", "storage_units"),
        "p_nom_set",
        _is_set,
        "PyPSA fails on *_nom_set for non-extendable components",
        fixed_only=True,
    ),
    _AttributeCheck(
        ("stores",), "e_nom_set", _is_set, "PyPSA fails on *_nom_set for non-extendable components", fixed_only=True
    ),
    _AttributeCheck(
        ("lines", "transformers"),
        "s_nom_set",
        _is_set,
        "PyPSA fails on *_nom_set for non-extendable components",
        fixed_only=True,
    ),
    _AttributeCheck(
        ("generators", "links", "processes", "storage_units"),
        "p_nom_mod",
        _is_nonzero,
        "modular expansion is not modelled",
        extendable_only=True,
    ),
    _AttributeCheck(("stores",), "e_nom_mod", _is_nonzero, "modular expansion is not modelled", extendable_only=True),
    _AttributeCheck(
        ("generators", "links", "processes", "storage_units", "stores"),
        "p_set",
        _is_set,
        "fixed dispatch is not modelled",
    ),
    _AttributeCheck(("storage_units",), "state_of_charge_set", _is_set, "fixed operation is not modelled"),
    _AttributeCheck(("storage_units",), "p_dispatch_set", _is_set, "fixed operation is not modelled"),
    _AttributeCheck(("storage_units",), "p_store_set", _is_set, "fixed operation is not modelled"),
    _AttributeCheck(("stores",), "e_set", _is_set, "fixed operation is not modelled"),
)

# Features that don't change the optimum on their own: warning.
_WARNING_CHECKS: tuple[_AttributeCheck, ...] = (
    _AttributeCheck(("lines", "transformers"), "v_ang_min", _is_finite, "deprecated and ignored by PyPSA >= 1.4.0"),
)


def _component_name(key: Any) -> str:
    """Component name from a static index entry or dynamic column (a tuple with scenarios)."""
    return str(key[-1] if isinstance(key, tuple) else key)


def _names(index: pd.Index) -> pd.Index:
    return index.get_level_values(-1) if isinstance(index, pd.MultiIndex) else index


def _quote(names: set[str]) -> str:
    listed = sorted(names)
    suffix = f" and {len(listed) - _MAX_NAMES} more" if len(listed) > _MAX_NAMES else ""
    return ", ".join(listed[:_MAX_NAMES]) + suffix


def _offending_names(network: Network, component_type: str, check: _AttributeCheck) -> set[str]:
    """Names of the components where the attribute differs from its default, as a static value or a series."""
    c = getattr(network.components, component_type)
    names: set[str] = set()
    extendable = (
        c.static[_EXTENDABLE_FLAGS[component_type]].to_numpy(dtype=bool)
        if component_type in _EXTENDABLE_FLAGS and len(c.static) > 0
        else None
    )
    if len(c.static) > 0 and check.attr in c.static.columns:
        mask = np.asarray(check.detected(c.static[check.attr]), dtype=bool)
        if check.extendable_only and extendable is not None:
            mask = mask & extendable
        if check.fixed_only and extendable is not None:
            mask = mask & ~extendable
        names.update(_component_name(key) for key in c.static.index[mask])
    if check.attr in c.dynamic.keys() and not check.fixed_only and not check.extendable_only:
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


def _numbered_columns(df: pd.DataFrame, prefix: str) -> dict[int, str]:
    """Columns named <prefix><digits> (bus2, delay1, ...), keyed by their number."""
    pattern = re.compile(rf"^{prefix}(\d+)$")
    return {int(m.group(1)): col for col in df.columns if (m := pattern.match(str(col)))}


def _multiport_findings(network: Network) -> list[str]:
    findings = []
    for component_type in ("links", "processes"):
        df = getattr(network.components, component_type).static
        if len(df) == 0:
            continue
        label = _LABELS[component_type]
        for number, col in sorted(_numbered_columns(df, "bus").items()):
            if number < MAX_MULTIPORT_BUSES:
                continue
            mask = df[col].fillna("").astype(str).str.strip() != ""
            if mask.any():
                findings.append(
                    f"{label} with {col} (only bus0 to bus{MAX_MULTIPORT_BUSES - 1} are modelled): "
                    f"{_quote({_component_name(k) for k in df.index[mask.to_numpy()]})}"
                )
        delay_cols = ["delay"] if "delay" in df.columns else []
        delay_cols += list(_numbered_columns(df, "delay").values())
        for col in delay_cols:
            mask = df[col].fillna(0) != 0
            if mask.any():
                findings.append(
                    f"{label} with {col} (delayed delivery is not modelled): "
                    f"{_quote({_component_name(k) for k in df.index[mask.to_numpy()]})}"
                )
    return findings


def primary_energy_attribute(network: Network) -> str:
    """Carrier attribute of the primary_energy global constraints (co2_emissions when there are none).

    The GEMS models carry a single emission_factor, so all primary_energy global constraints must share
    their carrier attribute (checked by check_converter_limitations).
    """
    glcs = network.global_constraints
    if len(glcs) == 0:
        return "co2_emissions"
    attributes = glcs.loc[glcs["type"] == "primary_energy", "carrier_attribute"].unique()
    return str(attributes[0]) if len(attributes) > 0 else "co2_emissions"


def _global_constraint_findings(network: Network) -> list[str]:
    findings: list[str] = []
    glcs = network.global_constraints
    if len(glcs) == 0:
        return findings
    for key, glc in glcs.iterrows():
        name = _component_name(key)
        constraint_type = glc["type"]
        if constraint_type not in SUPPORTED_GLOBAL_CONSTRAINT_TYPES:
            continue
        if glc["sense"] not in _SENSES:
            findings.append(f"GlobalConstraint '{name}' with sense '{glc['sense']}' (only <=, == and >=)")
        if pd.notna(glc.get("investment_period", np.nan)):
            findings.append(f"GlobalConstraint '{name}' with an investment_period (investment periods)")
        if constraint_type == "co2_atmosphere" and glc["carrier_attribute"] not in network.carriers.columns:
            findings.append(f"GlobalConstraint '{name}' on unknown carrier attribute '{glc['carrier_attribute']}'")
    primary = glcs.loc[glcs["type"] == "primary_energy", "carrier_attribute"].unique()
    if len(primary) > 1:
        findings.append(
            "primary_energy GlobalConstraints on several carrier attributes "
            f"({', '.join(sorted(map(str, primary)))}): the GEMS models carry a single emission factor"
        )
    if (glcs["type"] == "transmission_expansion_cost_limit").any():
        findings.extend(_transmission_cost_with_fom_findings(network))
    return findings


def _transmission_cost_with_fom_findings(network: Network) -> list[str]:
    """transmission_expansion_cost_limit weighs capacities by the annuity without fom_cost, while the GEMS
    line and link models only carry the periodized cost (annuity + fom_cost)."""
    findings = []
    for component_type in ("lines", "links"):
        df = getattr(network.components, component_type).static
        if len(df) == 0 or "fom_cost" not in df.columns:
            continue
        mask = df[_EXTENDABLE_FLAGS[component_type]].astype(bool) & (df["fom_cost"].fillna(0) != 0)
        if mask.any():
            findings.append(
                f"{_LABELS[component_type]} with fom_cost and a transmission_expansion_cost_limit GlobalConstraint "
                f"(the limit weighs capacities by capital cost without fom_cost): "
                f"{_quote({_component_name(k) for k in df.index[mask.to_numpy()]})}"
            )
    return findings


def _global_constraint_warnings(network: Network) -> list[str]:
    warnings: list[str] = []
    glcs = network.global_constraints
    if len(glcs) == 0:
        return warnings
    for key, glc in glcs.iterrows():
        name = _component_name(key)
        if glc["type"] not in SUPPORTED_GLOBAL_CONSTRAINT_TYPES:
            warnings.append(f"GlobalConstraint '{name}' of type '{glc['type']}' (PyPSA ignores this type too)")
    return warnings


def _network_findings(network: Network) -> list[str]:
    findings = []
    if len(network.investment_periods) > 0:
        findings.append(f"investment periods ({len(network.investment_periods)} periods)")
    try:
        resolve_time_weights(network)
    except ValueError as error:
        findings.append(str(error))

    findings.extend(_global_constraint_findings(network))
    findings.extend(_multiport_findings(network))

    transformers = network.components.transformers.static
    if len(transformers) > 0:
        mask = transformers["phase_shift_min"] < transformers["phase_shift_max"]
        if mask.any():
            names = {_component_name(k) for k in transformers.index[mask.to_numpy()]}
            findings.append(
                f"Transformers with phase_shift_min < phase_shift_max (optimised phase shifts change the KVL flows): "
                f"{_quote(names)}"
            )

    if network.has_risk_preference:
        findings.append("a risk preference (CVaR objective needs cross-scenario operators)")
    return findings


def check_converter_limitations(network: Network) -> None:
    """Raise ValueError if the network uses a feature that changes the optimum, and warn about the others."""
    for finding in _attribute_findings(network, _WARNING_CHECKS) + _global_constraint_warnings(network):
        logger.warning("Converter ignores %s.", finding)

    errors = _network_findings(network) + _attribute_findings(network, _ERROR_CHECKS)
    if errors:
        raise ValueError(
            "Converter does not support these PyPSA features, so the GEMS study would give different results:\n"
            + "\n".join(f"- {error}" for error in errors)
        )


def drop_inactive_components(network: Network) -> None:
    """Remove the components with active=False, as PyPSA leaves them out of the optimisation.

    Raises:
        ValueError: if a component is active in some scenarios only (PyPSA rejects it too).
    """
    dropped: list[str] = []
    for component_type, class_name in _CLASS_NAMES.items():
        static = getattr(network.components, component_type).static
        if len(static) == 0 or "active" not in static.columns:
            continue
        active = static["active"].fillna(True).astype(bool)
        by_name = active.groupby(_names(static.index)).agg(["all", "any"])
        varying = by_name.index[by_name["any"] & ~by_name["all"]]
        if len(varying) > 0:
            raise ValueError(
                f"{_LABELS[component_type]} active in some scenarios only (PyPSA rejects them too): "
                f"{_quote(set(map(str, varying)))}"
            )
        inactive = by_name.index[~by_name["any"]].tolist()
        if inactive:
            network.remove(class_name, inactive)
            dropped.append(f"{_LABELS[component_type]}: {_quote(set(map(str, inactive)))}")
    if dropped:
        logger.warning("Converter drops inactive components (active=False), as PyPSA does: %s", "; ".join(dropped))
