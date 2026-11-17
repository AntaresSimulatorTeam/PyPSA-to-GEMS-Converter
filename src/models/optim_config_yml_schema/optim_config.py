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
"""Schema of the antares-modeler 10.2 optim-config.yml.

See https://gems-energy.readthedocs.io/en/latest/3_User_Guide/3_GEMS_File_Structure/6_solver_optimization/
"""

from enum import Enum
from pathlib import Path
from typing import Any

import yaml
from pydantic import Field

from ..modified_base_model import ModifiedBaseModel


class ResolutionMode(str, Enum):
    SEQUENTIAL_SUBPROBLEMS = "sequential-subproblems"
    BENDERS_DECOMPOSITION = "benders-decomposition"


class Location(str, Enum):
    MASTER = "master"
    SUBPROBLEMS = "subproblems"
    MASTER_AND_SUBPROBLEMS = "master-and-subproblems"


class OutOfBoundsMode(str, Enum):
    CYCLIC = "cyclic"
    DROP = "drop"


class DecompositionEntry(ModifiedBaseModel):
    id: str
    location: Location


class ModelDecomposition(ModifiedBaseModel):
    variables: list[DecompositionEntry] | None = None
    constraints: list[DecompositionEntry] | None = None
    objective_contributions: list[DecompositionEntry] | None = None


class OutOfBoundsConstraint(ModifiedBaseModel):
    id: str
    mode: OutOfBoundsMode


class OutOfBoundsProcessing(ModifiedBaseModel):
    constraints: list[OutOfBoundsConstraint]


class ModelConfig(ModifiedBaseModel):
    id: str
    model_decomposition: ModelDecomposition | None = None
    out_of_bounds_processing: OutOfBoundsProcessing | None = None


class ScenarioScope(ModifiedBaseModel):
    include: list[str] | None = None
    exclude: list[str] | None = None


class OptimConfig(ModifiedBaseModel):
    resolution_mode: ResolutionMode
    # Always written, even empty: antares-modeler requires the key in a non-empty optim-config.yml.
    models: list[ModelConfig] = Field(default_factory=list)
    scenario_scope: ScenarioScope | None = None

    def to_dict(self) -> dict[str, Any]:
        """Kebab-case keys; unset optional fields are left out."""
        return self.model_dump(by_alias=True, exclude_none=True, mode="json")

    def to_yml(self, output_path: Path) -> None:
        with output_path.open("w", encoding="utf-8") as yaml_file:
            yaml.safe_dump(self.to_dict(), yaml_file, sort_keys=False)
