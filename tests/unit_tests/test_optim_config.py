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

from pathlib import Path
from typing import Any

import pytest
import yaml
from pypsa import Network

from src.models.optim_config_yml_schema import (
    DecompositionEntry,
    Location,
    ModelConfig,
    ModelDecomposition,
    OptimConfig,
    OutOfBoundsConstraint,
    OutOfBoundsMode,
    OutOfBoundsProcessing,
    ResolutionMode,
    ScenarioScope,
)
from src.optim_config_builder import (
    BENDERS_CAPACITY_VARIABLES,
    StudyContext,
    add_model_config,
    build_hybrid_optim_config,
    build_optim_config,
)
from src.study_type import StudyType, classify_study

LIBRARY_ID = "pypsa_models"
LIBRARY_PATH = Path(__file__).parents[2] / "resources" / "pypsa_models" / "pypsa_models.yml"
ALL_CAPACITY_MODEL_IDS = {f"{LIBRARY_ID}.{model}" for model in BENDERS_CAPACITY_VARIABLES}
USED_MODEL_IDS = {f"{LIBRARY_ID}.generator", f"{LIBRARY_ID}.bus", f"{LIBRARY_ID}.load"}


def _benders_entry(model: str, variable: str) -> dict[str, Any]:
    return {
        "id": f"{LIBRARY_ID}.{model}",
        "model-decomposition": {
            "variables": [{"id": variable, "location": "master-and-subproblems"}],
            "objective-contributions": [
                {"id": "capital_objective", "location": "master"},
                {"id": "operational_objective", "location": "subproblems"},
            ],
        },
    }


# Content of the former resources/optim-config.yml template, which the Benders rule replaces.
BENDERS_TEMPLATE = {
    "resolution-mode": "benders-decomposition",
    "models": [
        _benders_entry("generator", "p_nom"),
        _benders_entry("link", "p_nom"),
        _benders_entry("storage_unit", "p_nom"),
        _benders_entry("store", "e_nom"),
    ],
}


def _decomposition(variable: str, location: Location) -> ModelConfig:
    return ModelConfig(
        id=f"{LIBRARY_ID}.store",
        model_decomposition=ModelDecomposition(variables=[DecompositionEntry(id=variable, location=location)]),
    )


# --- schema ---


def test_schema_writes_kebab_case_keys_and_leaves_unset_fields_out(tmp_path: Path) -> None:
    optim_config = OptimConfig(
        resolution_mode=ResolutionMode.SEQUENTIAL_SUBPROBLEMS,
        models=[
            ModelConfig(
                id=f"{LIBRARY_ID}.store",
                out_of_bounds_processing=OutOfBoundsProcessing(
                    constraints=[OutOfBoundsConstraint(id="energy_balance", mode=OutOfBoundsMode.DROP)]
                ),
            )
        ],
        scenario_scope=ScenarioScope(include=["0-2"]),
    )
    optim_config.to_yml(tmp_path / "optim-config.yml")

    assert yaml.safe_load((tmp_path / "optim-config.yml").read_text(encoding="utf-8")) == {
        "resolution-mode": "sequential-subproblems",
        "models": [
            {
                "id": f"{LIBRARY_ID}.store",
                "out-of-bounds-processing": {"constraints": [{"id": "energy_balance", "mode": "drop"}]},
            }
        ],
        "scenario-scope": {"include": ["0-2"]},
    }


def test_schema_always_writes_models() -> None:
    assert OptimConfig(resolution_mode=ResolutionMode.SEQUENTIAL_SUBPROBLEMS).to_dict() == {
        "resolution-mode": "sequential-subproblems",
        "models": [],
    }


def test_add_model_config_merges_settings_of_the_same_model() -> None:
    optim_config = OptimConfig(resolution_mode=ResolutionMode.BENDERS_DECOMPOSITION)
    add_model_config(optim_config, _decomposition("e_nom", Location.MASTER_AND_SUBPROBLEMS))
    add_model_config(optim_config, _decomposition("e_nom", Location.MASTER_AND_SUBPROBLEMS))
    add_model_config(
        optim_config,
        ModelConfig(
            id=f"{LIBRARY_ID}.store",
            out_of_bounds_processing=OutOfBoundsProcessing(
                constraints=[OutOfBoundsConstraint(id="energy_balance", mode=OutOfBoundsMode.DROP)]
            ),
        ),
    )

    assert optim_config.to_dict()["models"] == [
        {
            "id": f"{LIBRARY_ID}.store",
            "model-decomposition": {"variables": [{"id": "e_nom", "location": "master-and-subproblems"}]},
            "out-of-bounds-processing": {"constraints": [{"id": "energy_balance", "mode": "drop"}]},
        }
    ]


def test_add_model_config_rejects_conflicting_settings() -> None:
    optim_config = OptimConfig(resolution_mode=ResolutionMode.BENDERS_DECOMPOSITION)
    add_model_config(optim_config, _decomposition("e_nom", Location.MASTER_AND_SUBPROBLEMS))
    with pytest.raises(ValueError, match="Conflicting optim-config.yml settings.*'e_nom'"):
        add_model_config(optim_config, _decomposition("e_nom", Location.SUBPROBLEMS))


# --- builder ---


@pytest.mark.parametrize(
    "study_type",
    [
        StudyType.OPERATIONAL_SINGLE_SCENARIO,
        StudyType.INVESTMENT_SINGLE_SCENARIO,
        StudyType.OPERATIONAL_MULTI_SCENARIO,
    ],
)
def test_builder_sequential_subproblems(study_type: StudyType) -> None:
    assert build_optim_config(study_type, LIBRARY_ID, USED_MODEL_IDS).to_dict() == {
        "resolution-mode": "sequential-subproblems",
        "models": [],
    }


def test_builder_multi_scenario_investment_reproduces_the_benders_template() -> None:
    # The template listed the four capacity models whatever the models used in the study.
    optim_config = build_optim_config(StudyType.INVESTMENT_MULTI_SCENARIO, LIBRARY_ID, USED_MODEL_IDS)
    assert optim_config.to_dict() == BENDERS_TEMPLATE


def test_hybrid_optim_config_is_the_benders_template() -> None:
    assert build_hybrid_optim_config(LIBRARY_ID).to_dict() == BENDERS_TEMPLATE


def test_builder_applies_extra_rules() -> None:
    def drop_rule(context: StudyContext) -> list[ModelConfig]:
        return [
            ModelConfig(
                id=f"{LIBRARY_ID}.store",
                out_of_bounds_processing=OutOfBoundsProcessing(
                    constraints=[OutOfBoundsConstraint(id="energy_balance", mode=OutOfBoundsMode.DROP)]
                ),
            )
        ]

    optim_config = build_optim_config(
        StudyType.OPERATIONAL_SINGLE_SCENARIO, LIBRARY_ID, USED_MODEL_IDS, rules=[drop_rule]
    )
    assert optim_config.to_dict()["models"] == [
        {
            "id": f"{LIBRARY_ID}.store",
            "out-of-bounds-processing": {"constraints": [{"id": "energy_balance", "mode": "drop"}]},
        }
    ]


def test_builder_ids_exist_in_the_model_library() -> None:
    """antares-modeler rejects an optim-config.yml that names an unknown model, variable or contribution."""
    library = yaml.safe_load(LIBRARY_PATH.read_text(encoding="utf-8"))["library"]
    models = {f"{library['id']}.{model['id']}": model for model in library["models"]}

    optim_config = build_hybrid_optim_config(LIBRARY_ID)
    assert {model_config.id for model_config in optim_config.models} == ALL_CAPACITY_MODEL_IDS
    for model_config in optim_config.models:
        model = models[model_config.id]
        decomposition = model_config.model_decomposition
        assert decomposition is not None
        sections = {
            "variables": decomposition.variables,
            "constraints": decomposition.constraints,
            "objective-contributions": decomposition.objective_contributions,
        }
        for section, entries in sections.items():
            library_ids = {item["id"] for item in model.get(section) or []}
            for entry in entries or []:
                assert entry.id in library_ids, f"{model_config.id}: unknown {section} id '{entry.id}'"


# --- study types ---


def _network(extendable: str | None, n_scenarios: int) -> Network:
    network = Network(snapshots=range(3))
    network.add("Bus", "a")
    network.add("Bus", "b")
    network.add("Load", "load", bus="a", p_set=10)
    network.add("Generator", "gen", bus="a", p_nom=50, marginal_cost=1, p_nom_extendable=extendable == "generators")
    network.add("Line", "line", bus0="a", bus1="b", x=0.1, s_nom=20, s_nom_extendable=extendable == "lines")
    if n_scenarios > 1:
        network.set_scenarios({f"s{i}": 1 / n_scenarios for i in range(n_scenarios)})
    return network


@pytest.mark.parametrize(
    ("extendable", "n_scenarios", "expected"),
    [
        (None, 1, StudyType.OPERATIONAL_SINGLE_SCENARIO),
        ("generators", 1, StudyType.INVESTMENT_SINGLE_SCENARIO),
        (None, 2, StudyType.OPERATIONAL_MULTI_SCENARIO),
        ("generators", 2, StudyType.INVESTMENT_MULTI_SCENARIO),
        # Extendable lines don't make a study an investment study (yet).
        ("lines", 1, StudyType.OPERATIONAL_SINGLE_SCENARIO),
        ("lines", 2, StudyType.OPERATIONAL_MULTI_SCENARIO),
    ],
)
def test_classify_study(extendable: str | None, n_scenarios: int, expected: StudyType) -> None:
    assert classify_study(_network(extendable, n_scenarios), n_scenarios) is expected
