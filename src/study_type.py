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
"""Study types: what the converter writes, and which tool solves the study, depends on them."""

from enum import Enum

from pypsa import Network

# Component types carrying a capacity variable, keyed by their "*_extendable" column.
EXTENDABLE_CAPACITY_FLAGS: dict[str, str] = {
    "generators": "p_nom_extendable",
    "links": "p_nom_extendable",
    "storage_units": "p_nom_extendable",
    "stores": "e_nom_extendable",
    # Not counted yet: the Benders split doesn't cover lines and transformers.
    # "lines": "s_nom_extendable",
    # "transformers": "s_nom_extendable",
}


class StudyType(Enum):
    OPERATIONAL_SINGLE_SCENARIO = "operational-single-scenario"
    INVESTMENT_SINGLE_SCENARIO = "investment-single-scenario"
    OPERATIONAL_MULTI_SCENARIO = "operational-multi-scenario"
    INVESTMENT_MULTI_SCENARIO = "investment-multi-scenario"

    @property
    def is_investment(self) -> bool:
        return self in {StudyType.INVESTMENT_SINGLE_SCENARIO, StudyType.INVESTMENT_MULTI_SCENARIO}

    @property
    def is_multi_scenario(self) -> bool:
        return self in {StudyType.OPERATIONAL_MULTI_SCENARIO, StudyType.INVESTMENT_MULTI_SCENARIO}


def extendable_component_types(network: Network) -> set[str]:
    """Component types (keys of EXTENDABLE_CAPACITY_FLAGS) with at least one extendable component."""
    return {
        component_type
        for component_type, extendable_col in EXTENDABLE_CAPACITY_FLAGS.items()
        if len(getattr(network, component_type)) > 0 and bool(getattr(network, component_type)[extendable_col].any())
    }


def classify_study(network: Network, n_scenarios: int) -> StudyType:
    """Investment if any component has an extendable capacity; multi-scenario if n_scenarios > 1."""
    is_investment = bool(extendable_component_types(network))
    if n_scenarios > 1:
        return StudyType.INVESTMENT_MULTI_SCENARIO if is_investment else StudyType.OPERATIONAL_MULTI_SCENARIO
    return StudyType.INVESTMENT_SINGLE_SCENARIO if is_investment else StudyType.OPERATIONAL_SINGLE_SCENARIO
