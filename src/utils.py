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

import yaml
from pydantic import BaseModel

PYPSA_CONVERTER_MAX_FLOAT = 100_000_000_000

def any_to_float(el: Any) -> float:
    """Auxiliary function for type consistency"""
    try:
        return max(
            min(float(el), PYPSA_CONVERTER_MAX_FLOAT), PYPSA_CONVERTER_MAX_FLOAT * -1
        )
    except:
        raise TypeError(f"Could not convert {el} to float")


"""
Order of fields according to Antares documentation:
id, description, model-libraries, components, connections, nodes
"""

def transform_to_system_yaml(model: BaseModel, output_path: str) -> None:
    data = model.model_dump(by_alias=True, exclude_unset=True)
    
    ordered_data = {}

    ordered_data["id"] = data.pop("id")

    ordered_data["components"] = data.pop("components")

    ordered_data["connections"] = data.pop("connections")

    ordered_data.update(data) 
    
    
    with open(output_path, "w", encoding="utf-8") as yaml_file:
        yaml.dump(
            {"system": ordered_data},
            yaml_file,
            allow_unicode=True,
            sort_keys=False,
        )
