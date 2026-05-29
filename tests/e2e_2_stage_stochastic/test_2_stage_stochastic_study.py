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

import json
import logging
import subprocess
import time
from pathlib import Path

import pytest
from pypsa import Network

from src.dependencies import (
    get_antares_dir_name,
    get_antares_problem_generator_bin,
    get_antares_xpansion_benders_bin,
    get_antares_xpansion_dir_name,
)
from src.pypsa_converter import PyPSAStudyConverter
from src.utils import configure_xpansion_slave_weights, prepare_benders_runtime_files

current_dir = Path(__file__).resolve().parents[2]
logger = logging.getLogger(__name__)
logging.basicConfig(level=logging.INFO)


@pytest.fixture(scope="function", autouse=True)
def check_binaries() -> None:
    """Skip the test if Antares Simulator or Antares Xpansion binaries are not present."""
    if not (current_dir / get_antares_dir_name()).is_dir():
        pytest.skip(
            "Antares Simulator binaries not found. "
            "Download from https://github.com/AntaresSimulatorTeam/Antares_Simulator/releases"
        )
    if not (current_dir / get_antares_xpansion_dir_name()).is_dir():
        pytest.skip(
            "Antares Xpansion binaries not found. "
            "Download from https://github.com/AntaresSimulatorTeam/antares-xpansion/releases"
        )


def _get_pypsa_total_objective(network: Network) -> float:
    objective = network.objective
    objective_constant = network.objective_constant
    assert objective is not None
    assert objective_constant is not None
    return objective + objective_constant


def _build_stochastic_test_network(scenario_weights: dict[str, float]) -> Network:
    """Tiny 2-bus expansion network with the given scenario probabilities (must sum to 1)."""
    if abs(sum(scenario_weights.values()) - 1.0) > 1e-9:
        raise ValueError(f"scenario_weights must sum to 1, got {sum(scenario_weights.values())}")

    network = Network(name="Simple_Network", snapshots=range(168))

    network.add("Carrier", "AC", co2_emissions=0)
    network.add("Bus", "bus 1", v_nom=220, carrier="AC")
    network.add("Bus", "bus 2", v_nom=220, carrier="AC")

    network.add("Load", "load1", bus="bus 1", p_set=[60 + (i % 24) for i in range(168)], q_set=0)
    network.add("Load", "load2", bus="bus 2", p_set=[40 + ((i + 6) % 24) for i in range(168)], q_set=0)

    base_p_max = [0.9 for _ in range(168)]

    network.add(
        "Generator",
        "gen1",
        bus="bus 1",
        p_nom_extendable=True,
        p_nom_min=140,
        marginal_cost=45,
        p_nom=140,
        p_max_pu=base_p_max,
        capital_cost=1000,
    )

    network.add(
        "Generator",
        "gen2",
        bus="bus 2",
        p_nom_extendable=True,
        p_nom_min=100,
        marginal_cost=55,
        p_nom=100,
        p_max_pu=base_p_max,
        capital_cost=900,
    )
    network.add(
        "Line",
        "line12",
        bus0="bus 1",
        bus1="bus 2",
        x=0.1,
        r=0.01,
        s_nom=200,
        s_nom_extendable=True,
        s_nom_min=200,
        capital_cost=100,
    )
    network.set_scenarios(scenario_weights)
    return network


def _run_pypsa_xpansion_e2e(
    tmp_path: Path,
    scenario_weights: dict[str, float],
    study_name: str,
    *,
    check_objective_against_pypsa: bool = True,
) -> Network:
    """Solve with PyPSA, convert, run problem-generator + Benders; assert objectives and investments match."""
    scenario_order = list(scenario_weights.keys())
    reference_scenario = scenario_order[0]

    pypsa_network = _build_stochastic_test_network(scenario_weights)
    pypsa_start = time.perf_counter()
    status, condition = pypsa_network.optimize(solver_name="cbc", include_objective_constant=True)
    pypsa_elapsed = time.perf_counter() - pypsa_start
    logger.info(
        "PyPSA stochastic solve (%s): status=%s condition=%s elapsed=%ss", study_name, status, condition, pypsa_elapsed
    )
    logger.info("PyPSA total objective: %s", _get_pypsa_total_objective(pypsa_network))
    assert status == "ok"
    assert condition == "optimal"

    network = _build_stochastic_test_network(scenario_weights)
    study_dir = tmp_path / study_name
    PyPSAStudyConverter(
        network,
        study_dir,
        ".tsv",
        solver_name="coin",
    ).to_gems_study()

    study_root = study_dir / "systems"
    assert (study_root / "study.antares").exists()
    assert (study_root / "user" / "expansion" / "options.json").exists()
    assert (study_root / "user" / "expansion" / "pypsa_scenario_weights.json").exists()

    problem_generator_bin = get_antares_problem_generator_bin(current_dir)
    result = subprocess.run(
        [str(problem_generator_bin), str(study_root)],
        capture_output=True,
        text=True,
        check=False,
        cwd=str(problem_generator_bin.parent),
    )
    assert result.returncode == 0, f"problem-generator failed:\n{result.stderr[-4000:]}"

    output_dir, options_path = prepare_benders_runtime_files(study_root)
    weight_mode = configure_xpansion_slave_weights(
        output_dir,
        options_path,
        scenario_weights,
        scenario_order=scenario_order,
    )
    logger.info("Xpansion SLAVE_WEIGHT file for %s: %s", study_name, weight_mode)

    benders_bin = get_antares_xpansion_benders_bin(current_dir)
    result = subprocess.run(
        [str(benders_bin), str(options_path.name)],
        capture_output=True,
        text=True,
        check=False,
        cwd=str(output_dir),
    )
    assert result.returncode == 0, f"benders failed:\n{result.stderr[-4000:]}"

    xpansion_result = json.loads((output_dir / "expansion" / "out.json").read_text(encoding="utf-8"))
    xpansion_solution = xpansion_result["solution"]
    pypsa_total_objective = _get_pypsa_total_objective(pypsa_network)

    logger.info(
        "Objective comparison (%s): pypsa=%s xpansion=%s",
        study_name,
        pypsa_total_objective,
        xpansion_solution["overall_cost"],
    )

    assert xpansion_solution["problem_status"] == "OPTIMAL"
    if check_objective_against_pypsa:
        assert xpansion_solution["overall_cost"] == pytest.approx(pypsa_total_objective)
    if weight_mode == "xpansion_slave_weights.txt":
        weights_text = (output_dir / "xpansion_slave_weights.txt").read_text(encoding="utf-8")
        for scenario, weight in scenario_weights.items():
            assert str(weight) in weights_text
        assert "WEIGHT_SUM 1" in weights_text
    assert xpansion_solution["values"]["generator_gen1.p_nom"] == pytest.approx(
        pypsa_network.generators.loc[(reference_scenario, "gen1"), "p_nom_opt"]
    )
    assert xpansion_solution["values"]["generator_gen2.p_nom"] == pytest.approx(
        pypsa_network.generators.loc[(reference_scenario, "gen2"), "p_nom_opt"]
    )
    return pypsa_network


def test_2_stage_stochastic_study_two_scenarios(tmp_path: Path) -> None:
    _run_pypsa_xpansion_e2e(
        tmp_path,
        {"low": 0.5, "high": 0.5},
        "test_2_stage_stochastic_study_two_scenarios",
    )


@pytest.mark.parametrize(
    "scenario_weights",
    [
        {"dry": 0.2, "normal": 0.5, "wet": 0.3},
        {"s1": 0.1, "s2": 0.2, "s3": 0.3, "s4": 0.4},
    ],
    ids=["three_scenarios", "four_scenarios"],
)
def test_2_stage_stochastic_study_weighted_scenarios(
    tmp_path: Path,
    scenario_weights: dict[str, float],
) -> None:
    """PyPSA vs Benders with 3 or 4 scenarios whose weights sum to 1."""
    study_name = f"test_2_stage_stochastic_{len(scenario_weights)}_scenarios"
    _run_pypsa_xpansion_e2e(
        tmp_path,
        scenario_weights,
        study_name,
        check_objective_against_pypsa=False,
    )
