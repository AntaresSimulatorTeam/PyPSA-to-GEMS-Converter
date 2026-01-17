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

import pandas as pd


@dataclass
class PyPSAComponentData:
    pypsa_model_id: str
    constant_data: pd.DataFrame
    time_dependent_data: dict[str, pd.DataFrame]
    gems_model_id: str
    pypsa_params_to_gems_params: dict[str, str]
    pypsa_params_to_gems_connections: dict[str, tuple[str, str]]

    def check_params_consistency(self) -> None:
        for key in self.pypsa_params_to_gems_params:
            self._check_key_in_constant_data(key)
        for key in self.pypsa_params_to_gems_connections:
            self._check_key_in_constant_data(key)

    def _check_key_in_constant_data(self, key: str) -> None:
        if key not in self.constant_data.columns:
            raise ValueError(
                f"Parameter {key} not available in constant data, defining all available parameters for model {self.pypsa_model_id}"
            )
