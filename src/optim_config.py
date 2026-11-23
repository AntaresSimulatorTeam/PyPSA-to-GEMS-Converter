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
"""optim-config.yml generated from the GEMS models used by a converted study.

Two sections are written per model, and only for the models actually instantiated in system.yml:

- ``out-of-bounds-processing``: the constraints referring to the previous time step that PyPSA does not wrap
  around the horizon (ramp limits, non-cyclic storage balances) are dropped at the first time step. Read by
  antares-modeler (10.1 and 10.2) and GemsPy (>= 0.1.3).
- ``model-decomposition`` (Benders / Antares-Xpansion only): capacity variables in master and subproblems,
  capital costs in the master, operational costs in the subproblems. antares-modeler requires the key on every
  listed model, so an empty mapping is written when there is nothing to decompose.

Operational studies without such constraints get no optim-config.yml (unchanged behaviour).
"""

from collections.abc import Iterable
from typing import Any

# Constraints dropped at the first time step, per GEMS model (see pypsa_models.yml).
_DROPPED_CONSTRAINTS: dict[str, tuple[str, ...]] = {
    "generator_ramp": ("ramp_up", "ramp_down"),
    "link_ramp": ("ramp_up", "ramp_down"),
    "link_multiport_ramp": ("ramp_up", "ramp_down"),
    "storage_unit_non_cyclic": ("state_of_charge_balance",),
    "store_non_cyclic": ("energy_balance",),
}

# Capacity (investment) variable of each GEMS model decomposed by Benders.
_CAPACITY_VARIABLES: dict[str, str] = {
    "generator": "p_nom",
    "generator_ramp": "p_nom",
    "link": "p_nom",
    "link_ramp": "p_nom",
    "link_multiport": "p_nom",
    "link_multiport_ramp": "p_nom",
    "storage_unit": "p_nom",
    "storage_unit_non_cyclic": "p_nom",
    "store": "e_nom",
    "store_non_cyclic": "e_nom",
}

# Global constraints on capacities only: their constraint holds master variables only.
_MASTER_CONSTRAINT_MODELS = (
    "global_constraint_capacity_max",
    "global_constraint_capacity_eq",
    "global_constraint_capacity_min",
)


def _decomposition(model_id: str) -> dict[str, Any]:
    if model_id in _CAPACITY_VARIABLES:
        return {
            "variables": [{"id": _CAPACITY_VARIABLES[model_id], "location": "master-and-subproblems"}],
            "objective-contributions": [
                {"id": "capital_objective", "location": "master"},
                {"id": "operational_objective", "location": "subproblems"},
            ],
        }
    if model_id in _MASTER_CONSTRAINT_MODELS:
        return {"binding-constraints": [{"id": "constraint_expression", "location": "master"}]}
    return {}


def build_optim_config(library_id: str, model_ids: Iterable[str], benders: bool) -> dict[str, Any] | None:
    """optim-config.yml content for a study instantiating the given models, or None if none is needed."""
    models: list[dict[str, Any]] = []
    for model_id in sorted(set(model_ids)):
        decomposition = _decomposition(model_id) if benders else {}
        dropped = _DROPPED_CONSTRAINTS.get(model_id, ())
        if not decomposition and not dropped:
            continue
        entry: dict[str, Any] = {"id": f"{library_id}.{model_id}", "model-decomposition": decomposition}
        if dropped:
            entry["out-of-bounds-processing"] = {
                "constraints": [{"id": constraint, "mode": "drop"} for constraint in dropped]
            }
        models.append(entry)
    if benders:
        return {"resolution-mode": "benders-decomposition", "models": models}
    if not models:
        return None
    return {"models": models}
