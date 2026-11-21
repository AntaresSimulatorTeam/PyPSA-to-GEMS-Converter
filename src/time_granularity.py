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
``generators``   weights ``e_sum_min``/``e_sum_max``, the primary-energy and the
                 operational-limit global constraints
===============  ======================================================================

Each GEMS model reads the column PyPSA reads for the same component: ``hours_per_time_step`` is
``generators`` for generator models and ``stores`` for storage models, and ``objective_weighting`` is
``objective`` everywhere. The columns may differ from each other and may vary over snapshots (temporal
clustering, segmentation): a constant column is written as a scalar, a varying one as a time series.

The granularity is read from ``snapshot_weightings`` only, never inferred from the snapshot index:
PyPSA itself treats a weighting of 1 as one hour whatever the index says.

The standing loss of storage is applied as ``(1 - standing_loss) ** stores_weighting`` by PyPSA. Antares
Modeler only accepts constant exponents, so the converter computes this ``standing_efficiency`` itself.
"""

import numpy as np
import pandas as pd
from pypsa import Network

HOURS_PER_TIME_STEP = "hours_per_time_step"
OBJECTIVE_WEIGHTING = "objective_weighting"
STANDING_EFFICIENCY = "standing_efficiency"

# For each component type with time-granularity parameters in its GEMS models: the parameters and the
# snapshot_weightings column each one is read from. Add a type here when its GEMS models gain one of them.
TIME_WEIGHT_COMPONENTS: dict[str, dict[str, str]] = {
    "generators": {HOURS_PER_TIME_STEP: "generators", OBJECTIVE_WEIGHTING: "objective"},
    "links": {OBJECTIVE_WEIGHTING: "objective"},
    "processes": {OBJECTIVE_WEIGHTING: "objective"},
    "storage_units": {HOURS_PER_TIME_STEP: "stores", OBJECTIVE_WEIGHTING: "objective"},
    "stores": {HOURS_PER_TIME_STEP: "stores", OBJECTIVE_WEIGHTING: "objective"},
}


def _has_components(pypsa_network: Network, *component_types: str) -> bool:
    return any(len(getattr(pypsa_network, component_type)) > 0 for component_type in component_types)


def _relevant_columns(pypsa_network: Network) -> set[str]:
    """snapshot_weightings columns PyPSA actually reads for this network."""
    columns: set[str] = set()
    for component_type, params in TIME_WEIGHT_COMPONENTS.items():
        if _has_components(pypsa_network, component_type):
            columns.update(params.values())
    return columns


def resolve_time_weights(pypsa_network: Network) -> None:
    """Validate the snapshot weightings the network actually reads.

    A column no component reads cannot make the conversion fail: a network without storage never reads
    "stores", and one without generators never reads "generators".

    Raises:
        ValueError: if a relevant column has a NaN or infinite value, if a physical duration ("stores",
            "generators") is not positive, or if an objective weighting is negative.
    """
    weightings = pypsa_network.snapshot_weightings
    for column in sorted(_relevant_columns(pypsa_network)):
        values = weightings[column]
        if not np.isfinite(values).all():
            raise ValueError(
                f"Converter supports only finite snapshot weightings, but 'snapshot_weightings.{column}' "
                f"contains NaN or infinite values."
            )
        if column == "objective":
            if (values < 0).any():
                raise ValueError(
                    "Converter supports only non-negative snapshot weightings, but "
                    f"'snapshot_weightings.objective' has a minimum of {values.min()}."
                )
        elif (values <= 0).any():
            raise ValueError(
                f"Converter supports only a positive time step duration, but 'snapshot_weightings.{column}' "
                f"has a minimum of {values.min()}."
            )


def _is_constant(values: pd.Series) -> bool:
    return bool(np.isclose(values, values.iloc[0]).all())


def shared_series_key(column: str) -> str:
    """Key of the shared data series holding a time-varying snapshot_weightings column."""
    return f"snapshot_weightings_{column}"


def add_time_weights(pypsa_network: Network) -> dict[str, dict[str, str]]:
    """Add the time-granularity parameters to every component type that needs them.

    A column constant over snapshots is written as a static column. A varying one is shared by every
    component, so it is written once as a shared data series: its static column only holds the first value
    (every registered parameter must exist as a static column) and the returned mapping
    {component_type: {param: shared series key}} tells the register to reference the shared series.
    """
    weightings = pypsa_network.snapshot_weightings
    shared: dict[str, dict[str, str]] = {}
    for component_type, params in TIME_WEIGHT_COMPONENTS.items():
        if not _has_components(pypsa_network, component_type):
            continue
        static = getattr(pypsa_network.components, component_type).static
        for param, column in params.items():
            values = weightings[column].astype(float)
            static[param] = float(values.iloc[0])
            if not _is_constant(values):
                shared.setdefault(component_type, {})[param] = shared_series_key(column)
    return shared


def time_weight_series(pypsa_network: Network) -> dict[str, list[float]]:
    """Values of every time-varying snapshot_weightings column, keyed by shared series key."""
    weightings = pypsa_network.snapshot_weightings
    return {
        shared_series_key(column): weightings[column].astype(float).tolist()
        for column in weightings.columns
        if not _is_constant(weightings[column].astype(float))
    }


def write_component_frame(pypsa_network: Network, component_type: str, param: str, frame: pd.DataFrame) -> None:
    """Store a per-component parameter given over snapshots (snapshots x static index).

    The static column holds the first snapshot's value; only the components whose value varies over
    snapshots get a dynamic column, so constant components keep a scalar in system.yml.
    """
    component = getattr(pypsa_network.components, component_type)
    static = component.static
    frame = frame.reindex(columns=static.index)
    first = frame.iloc[0]
    static[param] = first.to_numpy()
    varying = ~np.isclose(frame.to_numpy(), first.to_numpy()[None, :], equal_nan=True).all(axis=0)
    if varying.any():
        dynamic = frame.loc[:, varying]
        dynamic.columns.names = static.index.names
        component.dynamic[param] = dynamic


def add_standing_efficiency(pypsa_network: Network, component_type: str) -> None:
    """Add ``standing_efficiency = (1 - standing_loss) ** stores_weighting`` to stores or storage units."""
    if not _has_components(pypsa_network, component_type):
        return
    pypsa_type = {"stores": "Store", "storage_units": "StorageUnit"}[component_type]
    standing_loss = pypsa_network.get_switchable_as_dense(pypsa_type, "standing_loss")
    hours = pypsa_network.snapshot_weightings["stores"].astype(float)
    efficiency = (1.0 - standing_loss).pow(hours, axis=0)
    write_component_frame(pypsa_network, component_type, STANDING_EFFICIENCY, efficiency)
