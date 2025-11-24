# Copyright (c) 2024, RTE (https://www.rte-france.com)
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

from typing import Any

PYPSA_CONVERTER_MAX_FLOAT = 100_000_000_000

def any_to_float(el: Any) -> float:
    """Auxiliary function for type consistency"""
    try:
        return max(
            min(float(el), PYPSA_CONVERTER_MAX_FLOAT), PYPSA_CONVERTER_MAX_FLOAT * -1
        )
    except:
        raise TypeError(f"Could not convert {el} to float")