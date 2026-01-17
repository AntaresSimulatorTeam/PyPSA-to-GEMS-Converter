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

from dataclasses import dataclass


@dataclass
class PyPSAGlobalConstraintData:
    pypsa_name: str
    # pypsa_investment_period
    pypsa_carrier_attribute: str
    pypsa_sense: str
    pypsa_constant: float
    gems_model_id: str  # gems model for this GlobalConstraint
    gems_port_id: str  # gems port for this GlobalConstraint
    gems_components_and_ports: list[tuple[str, str]]
