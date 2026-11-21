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

from dataclasses import dataclass, field


@dataclass
class PyPSAGlobalConstraintData:
    """A GEMS global constraint component and the component ports it sums.

    One PyPSA GlobalConstraint (or one bus nom_min_/nom_max_<carrier> limit) gives one component, whatever the
    number of scenarios: its quota is scenario-dependent when PyPSA's constant differs between scenarios.
    """

    gems_component_id: str
    gems_model_id: str  # gems model for this GlobalConstraint
    gems_port_id: str  # gems port for this GlobalConstraint
    quota_by_scenario: dict[str, float]
    gems_components_and_ports: list[tuple[str, str]] = field(default_factory=list)
    pypsa_type: str = ""
