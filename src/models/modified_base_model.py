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

from pydantic import BaseModel, ConfigDict


class ModifiedBaseModel(BaseModel):
    model_config = ConfigDict(
        alias_generator=lambda name: name.replace("_", "-"),
        extra="forbid",
        validate_by_alias=True,
        validate_by_name=True,
    )
