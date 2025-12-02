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

from pydantic import PrivateAttr
from ..modified_base_model import ModifiedBaseModel
import yaml

class ModelerParameters(ModifiedBaseModel):
    solver: str = "highs"
    solver_logs: bool = False
    solver_parameters: str = "THREADS 1"
    no_output: bool = False
    _first_time_step: int = PrivateAttr(default=None)
    _last_time_step: int = PrivateAttr(default=None)


    def __init__(self, solver: str, solver_logs: bool, solver_parameters: str, no_output: bool, first_time_step: int, last_time_step: int):
        super().__init__()
        self.solver = solver
        self.solver_logs = solver_logs
        self.solver_parameters = solver_parameters
        self.no_output = no_output
        self._first_time_step = first_time_step
        self._last_time_step = last_time_step

    def to_dict(self, by_alias: bool = True, exclude_unset: bool = True) -> dict:
        """Convert ModelerParameters object to dictionary, handling PrivateAttr fields."""
        return {
            "solver": self.solver,
            "solver-logs": self.solver_logs,
            "solver-parameters": self.solver_parameters,
            "no-output": self.no_output,
            "first-time-step": self._first_time_step,
            "last-time-step": self._last_time_step,
        }


    def to_yaml(self, output_path: str) -> None:
        converted_data = self.to_dict(by_alias=True, exclude_unset=True)

        with open(output_path, "w", encoding="utf-8") as yaml_file:
            yaml.dump(
                converted_data,
                yaml_file,
                allow_unicode=True,
                sort_keys=False,
            )
    
    def get_first_time_step(self) -> int:
        return self._first_time_step

    def get_last_time_step(self) -> int:
        return self._last_time_step