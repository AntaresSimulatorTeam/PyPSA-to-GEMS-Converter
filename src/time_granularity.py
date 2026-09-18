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
"""Mapping of PyPSA snapshot weightings onto the GEMS time-granularity parameters.

PyPSA stores the time resolution in ``network.snapshot_weightings``, a DataFrame with three columns
that enter the optimisation problem differently:

===============  ======================================================================
Column           Where PyPSA reads it
===============  ======================================================================
``objective``    scales ``marginal_cost``, ``marginal_cost_storage`` and ``spill_cost``
                 in the objective (never ``capital_cost``)
``stores``       elapsed hours in the ``Store`` **and** ``StorageUnit`` energy balances
                 (there is no ``storage_units`` column)
``generators``   weights ``e_sum_min``/``e_sum_max`` and the CO2 primary-energy constraint
===============  ======================================================================

``stores`` and ``generators`` are both the physical duration of a time step, so they map onto a
single GEMS parameter ``hours_per_time_step``; ``objective`` maps onto ``objective_weighting``, which
PyPSA allows to differ (e.g. operational costs annualised over a representative period).

The granularity is read from ``snapshot_weightings`` only, never inferred from the spacing of the
snapshot index: PyPSA itself treats a weighting of 1 as one hour whatever the index says.
"""

from pypsa import Network

from src.utils import any_to_float

HOURS_PER_TIME_STEP = "hours_per_time_step"
OBJECTIVE_WEIGHTING = "objective_weighting"

# Component types that have time-granularity parameters in the GEMS model, and which ones.
# Add a type here when its GEMS model gains one of them.
TIME_WEIGHT_COMPONENTS: dict[str, tuple[str, ...]] = {
    "generators": (HOURS_PER_TIME_STEP, OBJECTIVE_WEIGHTING),
    "links": (OBJECTIVE_WEIGHTING,),
    "storage_units": (HOURS_PER_TIME_STEP, OBJECTIVE_WEIGHTING),
    "stores": (HOURS_PER_TIME_STEP, OBJECTIVE_WEIGHTING),
}


def _has_components(pypsa_network: Network, *component_types: str) -> bool:
    return any(len(getattr(pypsa_network, component_type)) > 0 for component_type in component_types)


def resolve_time_weights(pypsa_network: Network) -> dict[str, float]:
    """Validate the snapshot weightings and resolve the GEMS time-granularity parameters.

    Only the columns PyPSA actually reads for this network are validated, so a column no component
    reads cannot make the conversion fail: a network without storage never reads "stores", and one
    without generators never reads "generators". Rejecting those would refuse a network that PyPSA
    itself solves without complaint.

    Each relevant column must be constant over snapshots, since GEMS receives a single value.
    "stores" and "generators" are both the physical duration of a time step, so they must agree
    whenever both are read; otherwise hours_per_time_step comes from the one that is.

    Raises:
        ValueError: if a relevant column varies over snapshots, if the two physical columns disagree
            while both are read, or if a resolved weighting is out of range.
    """
    weightings = pypsa_network.snapshot_weightings
    has_storage = _has_components(pypsa_network, "stores", "storage_units")
    has_generators = _has_components(pypsa_network, "generators")
    # Every model with an operational_objective contribution reads the objective weighting.
    has_operational_cost = _has_components(pypsa_network, "generators", "links", "stores", "storage_units")

    for column, is_relevant in (
        ("stores", has_storage),
        ("generators", has_generators),
        ("objective", has_operational_cost),
    ):
        if not is_relevant:
            continue
        values = weightings[column]
        if values.nunique() != 1:
            raise ValueError(
                f"Converter supports only snapshot weightings that are constant over snapshots, "
                f"but 'snapshot_weightings.{column}' takes {values.nunique()} distinct values "
                f"(between {values.min()} and {values.max()})."
            )

    if has_storage and has_generators:
        stores_weighting = float(weightings["stores"].iloc[0])
        generators_weighting = float(weightings["generators"].iloc[0])
        if stores_weighting != generators_weighting:
            raise ValueError(
                f"Converter models a single time step duration, so 'snapshot_weightings.stores' "
                f"({stores_weighting}) and 'snapshot_weightings.generators' ({generators_weighting}) "
                f"must be equal when the network has both storage and generators."
            )
        hours_per_time_step = stores_weighting
    elif has_storage:
        hours_per_time_step = float(weightings["stores"].iloc[0])
    elif has_generators:
        hours_per_time_step = float(weightings["generators"].iloc[0])
    else:
        # No component carries hours_per_time_step; the value is never written.
        hours_per_time_step = 1.0

    objective_weighting = float(weightings["objective"].iloc[0]) if has_operational_cost else 1.0

    if hours_per_time_step <= 0:
        raise ValueError(
            f"Converter supports only a positive time step duration, but the snapshot weighting it is "
            f"read from is {hours_per_time_step}."
        )
    if objective_weighting < 0:
        raise ValueError(
            f"Converter supports only non-negative snapshot weightings, but "
            f"'snapshot_weightings.objective' is {objective_weighting}."
        )

    return {
        HOURS_PER_TIME_STEP: any_to_float(hours_per_time_step),
        OBJECTIVE_WEIGHTING: any_to_float(objective_weighting),
    }


def add_time_weights(pypsa_network: Network, values: dict[str, float]) -> None:
    """Add the resolved time-granularity parameters as static columns on every component that needs them.

    The weightings are constant over snapshots (enforced by resolve_time_weights) and are not
    scenario-dependent in PyPSA, so a scalar column is enough. GemsStudyWriter then writes them as
    plain values in system.yml rather than as time series.
    """
    for component_type, params in TIME_WEIGHT_COMPONENTS.items():
        df = getattr(pypsa_network, component_type)
        if len(df) == 0:
            continue
        for param in params:
            df[param] = values[param]
