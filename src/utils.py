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

import copy
from enum import Enum
from typing import Any

import pandas as pd
from pypsa import Network

PYPSA_CONVERTER_MAX_FLOAT = 100_000_000_000


def any_to_float(el: Any) -> float:
    """Auxiliary function for type consistency"""
    try:
        return max(min(float(el), PYPSA_CONVERTER_MAX_FLOAT), PYPSA_CONVERTER_MAX_FLOAT * -1)
    except (TypeError, ValueError):
        raise TypeError(f"Could not convert {el} to float")


def check_time_series_format(series_file_format: str) -> str:
    if series_file_format not in {".csv", ".tsv", "csv", "tsv"}:
        raise ValueError(f"Invalid series file format: {series_file_format}")

    if series_file_format in {"csv", "tsv"}:
        return "." + series_file_format

    return series_file_format


def convert_pypsa_version_to_integer(pypsa_version: str) -> int:
    """
    Convert PyPSA version to integer, example: 1.0.0 -> 100
    """
    decomposed_version = pypsa_version.split(".")
    return int("".join(decomposed_version))


class StudyType(Enum):
    WITH_SCENARIOS = 1


def determine_pypsa_study_type(pypsa_network: Network) -> tuple[StudyType, Network, dict[str, float]]:
    """Determine study type; studies without scenarios get one default scenario so we always convert as WITH_SCENARIOS."""
    if hasattr(pypsa_network, "has_scenarios") and pypsa_network.has_scenarios:
        return StudyType.WITH_SCENARIOS, pypsa_network, pypsa_network.scenario_weightings
    # Snapshot carrier co2_emissions before set_scenarios; PyPSA may overwrite/reset them after expansion.
    if hasattr(pypsa_network, "carriers") and "co2_emissions" in getattr(pypsa_network.carriers, "columns", []):
        carriers_df = pypsa_network.carriers
        idx = carriers_df.index
        names = idx.get_level_values(-1) if isinstance(idx, pd.MultiIndex) else idx
        pypsa_network._carrier_co2_snapshot = {
            str(names[i]): float(carriers_df["co2_emissions"].iloc[i])
            for i in range(len(carriers_df))
        }
    else:
        pypsa_network._carrier_co2_snapshot = {}
    # No scenarios: add single default scenario so all studies use the same multi-index path
    pypsa_network.set_scenarios({"default": 1})
    return StudyType.WITH_SCENARIOS, pypsa_network, pypsa_network.scenario_weightings
