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
"""Build optim-config.yml from the study type and the models a study uses.

The resolution mode comes from the study type. Per-model settings come from rules: each rule looks at the
study and returns ModelConfig entries; entries for the same model are merged, and conflicting values raise.
A new study feature (scenario scope, non-cyclic storage, ...) adds a rule or a resolution-mode entry.
"""

from collections.abc import Callable, Iterable
from dataclasses import dataclass
from typing import TypeVar

from src.models.optim_config_yml_schema import (
    DecompositionEntry,
    Location,
    ModelConfig,
    ModelDecomposition,
    OptimConfig,
    OutOfBoundsConstraint,
    OutOfBoundsProcessing,
    ResolutionMode,
)
from src.study_type import StudyType


@dataclass(frozen=True)
class StudyContext:
    study_type: StudyType
    library_id: str
    # Full model ids ("<library_id>.<model>") of the components in system.yml.
    used_model_ids: frozenset[str]


ModelConfigRule = Callable[[StudyContext], Iterable[ModelConfig]]

_RESOLUTION_MODES: dict[StudyType, ResolutionMode] = {
    StudyType.OPERATIONAL_SINGLE_SCENARIO: ResolutionMode.SEQUENTIAL_SUBPROBLEMS,
    # One scenario: capacities and operation fit in one problem, nothing to decompose.
    StudyType.INVESTMENT_SINGLE_SCENARIO: ResolutionMode.SEQUENTIAL_SUBPROBLEMS,
    # Solved on the hybrid study; systems/ alone solves scenario 0 only.
    StudyType.OPERATIONAL_MULTI_SCENARIO: ResolutionMode.SEQUENTIAL_SUBPROBLEMS,
    StudyType.INVESTMENT_MULTI_SCENARIO: ResolutionMode.BENDERS_DECOMPOSITION,
}

# Capacity variable of each model of pypsa_models.yml that a Benders master decides.
# Lines and transformers are left out: their extendable capacity doesn't make a study an investment study yet.
BENDERS_CAPACITY_VARIABLES: dict[str, str] = {
    "generator": "p_nom",
    "link": "p_nom",
    "storage_unit": "p_nom",
    "store": "e_nom",
}


def benders_capacity_configs(library_id: str) -> list[ModelConfig]:
    """Benders split of each capacity model: capacity in master and subproblems, capital cost in master."""
    return [
        ModelConfig(
            id=f"{library_id}.{model}",
            model_decomposition=ModelDecomposition(
                variables=[DecompositionEntry(id=variable, location=Location.MASTER_AND_SUBPROBLEMS)],
                objective_contributions=[
                    DecompositionEntry(id="capital_objective", location=Location.MASTER),
                    DecompositionEntry(id="operational_objective", location=Location.SUBPROBLEMS),
                ],
            ),
        )
        for model, variable in BENDERS_CAPACITY_VARIABLES.items()
    ]


def _benders_decomposition_rule(context: StudyContext) -> list[ModelConfig]:
    if _RESOLUTION_MODES[context.study_type] is not ResolutionMode.BENDERS_DECOMPOSITION:
        return []
    return benders_capacity_configs(context.library_id)


MODEL_CONFIG_RULES: tuple[ModelConfigRule, ...] = (_benders_decomposition_rule,)


def build_optim_config(
    study_type: StudyType,
    library_id: str,
    used_model_ids: Iterable[str],
    rules: Iterable[ModelConfigRule] = MODEL_CONFIG_RULES,
) -> OptimConfig:
    context = StudyContext(study_type, library_id, frozenset(used_model_ids))
    optim_config = OptimConfig(resolution_mode=_RESOLUTION_MODES[study_type])
    for rule in rules:
        for model_config in rule(context):
            add_model_config(optim_config, model_config)
    return optim_config


def build_hybrid_optim_config(library_id: str) -> OptimConfig:
    """optim-config.yml of the hybrid Antares study, whatever the study type.

    antares-solver's hybrid loader refuses sequential-subproblems as soon as a model has a
    scenario-independent variable, which every capacity model has (see src/antares_hybrid_writer.py).
    """
    optim_config = OptimConfig(resolution_mode=ResolutionMode.BENDERS_DECOMPOSITION)
    for model_config in benders_capacity_configs(library_id):
        add_model_config(optim_config, model_config)
    return optim_config


def add_model_config(optim_config: OptimConfig, model_config: ModelConfig) -> None:
    """Add model_config to optim_config, merged with any entry for the same model id."""
    existing = next((m for m in optim_config.models if m.id == model_config.id), None)
    if existing is None:
        optim_config.models.append(model_config.model_copy(deep=True))
        return
    if model_config.model_decomposition is not None:
        if existing.model_decomposition is None:
            existing.model_decomposition = ModelDecomposition()
        for section in ("variables", "constraints", "objective_contributions"):
            merged = _merge_by_id(
                getattr(existing.model_decomposition, section),
                getattr(model_config.model_decomposition, section),
                f"{model_config.id} model-decomposition {section}",
            )
            setattr(existing.model_decomposition, section, merged)
    if model_config.out_of_bounds_processing is not None:
        constraints = _merge_by_id(
            existing.out_of_bounds_processing.constraints if existing.out_of_bounds_processing else None,
            model_config.out_of_bounds_processing.constraints,
            f"{model_config.id} out-of-bounds-processing constraints",
        )
        existing.out_of_bounds_processing = OutOfBoundsProcessing(constraints=constraints or [])


_Entry = TypeVar("_Entry", DecompositionEntry, OutOfBoundsConstraint)


def _merge_by_id(current: list[_Entry] | None, new: list[_Entry] | None, where: str) -> list[_Entry] | None:
    """Union of two entry lists keyed by id; the same id with two different values raises ValueError."""
    if new is None:
        return current
    merged = {entry.id: entry for entry in current or []}
    for entry in new:
        if entry.id in merged and merged[entry.id] != entry:
            raise ValueError(
                f"Conflicting optim-config.yml settings for {where} '{entry.id}': {merged[entry.id]} vs {entry}"
            )
        merged.setdefault(entry.id, entry.model_copy())
    return list(merged.values())
