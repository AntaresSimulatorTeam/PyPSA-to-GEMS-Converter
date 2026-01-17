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

from typing import List, Optional

from ..modified_base_model import ModifiedBaseModel
from .gems_component_parameter import GemsComponentParameter


class GemsComponent(ModifiedBaseModel):
    id: str
    model: str
    scenario_group: Optional[str] = None
    parameters: Optional[List[GemsComponentParameter]] = None
