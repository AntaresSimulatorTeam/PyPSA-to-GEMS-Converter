# Copyright (c) 2025, RTE (https://www.rte-france.com)
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

from enum import Enum
from typing import Any

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
    LINEAR_OPTIMAL_POWER_FLOW = 1
    TWO_STAGE_STOCHASTIC = 2


def determine_pypsa_study_type(pypsa_network: Network) -> StudyType:
    study_version = convert_pypsa_version_to_integer(pypsa_network.pypsa_version)

    if study_version >= 100 and hasattr(pypsa_network, "has_scenarios") and pypsa_network.has_scenarios:
        return StudyType.TWO_STAGE_STOCHASTIC

    return StudyType.LINEAR_OPTIMAL_POWER_FLOW
