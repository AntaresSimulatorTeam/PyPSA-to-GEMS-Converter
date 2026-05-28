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
from src.utils import prepare_benders_runtime_files

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


def _build_two_stage_test_network() -> Network:
    network = Network(name="Simple_Network", snapshots=range(168))

    network.add("Carrier", "AC", co2_emissions=0)
    network.add("Bus", "bus 1", v_nom=220, carrier="AC")
    network.add("Bus", "bus 2", v_nom=220, carrier="AC")

    network.add("Load", "load1", bus="bus 1", p_set=[60 + (i % 24) for i in range(168)], q_set=0)
    network.add("Load", "load2", bus="bus 2", p_set=[40 + ((i + 6) % 24) for i in range(168)], q_set=0)

    # Keep the tiny study feasible even before any further investment.
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
    network.set_scenarios({"low": 0.5, "high": 0.5})
    return network


def test_2_stage_stochastic_study(tmp_path: Path) -> None:
    pypsa_network = _build_two_stage_test_network()
    pypsa_start = time.perf_counter()
    status, condition = pypsa_network.optimize(solver_name="highs", include_objective_constant=True)
    pypsa_elapsed = time.perf_counter() - pypsa_start
    logger.info("==============================================")
    logger.info("PyPSA stochastic solve:")
    logger.info("status: %s", status)
    logger.info("condition: %s", condition)
    logger.info("elapsed_seconds: %s", pypsa_elapsed)
    logger.info("objective: %s", pypsa_network.objective)
    logger.info("objective_constant: %s", pypsa_network.objective_constant)
    logger.info("total_objective: %s", _get_pypsa_total_objective(pypsa_network))
    logger.info("==============================================")
    assert status == "ok"
    assert condition == "optimal"

    network = _build_two_stage_test_network()
    study_dir = tmp_path / "test_2_stage_stochastic_study"
    PyPSAStudyConverter(
        network,
        study_dir,
        ".tsv",
        solver_name="coin",
    ).to_gems_study()

    study_root = study_dir / "systems"
    assert (study_root / "study.antares").exists()
    assert (study_root / "settings" / "generaldata.ini").exists()
    assert (study_root / "user" / "expansion" / "settings.ini").exists()
    assert (study_root / "user" / "expansion" / "options.json").exists()

    problem_generator_bin = get_antares_problem_generator_bin(current_dir)
    problem_generator_start = time.perf_counter()
    result = subprocess.run(
        [str(problem_generator_bin), str(study_root)],
        capture_output=True,
        text=True,
        check=False,
        cwd=str(problem_generator_bin.parent),
    )
    problem_generator_elapsed = time.perf_counter() - problem_generator_start
    logger.info("==============================================")
    logger.info("Problem generator output:")
    logger.info("elapsed_seconds: %s", problem_generator_elapsed)
    logger.info("returncode: %s", result.returncode)
    logger.info("stdout: %s", result.stdout)
    logger.info("stderr: %s", result.stderr)
    logger.info("==============================================")
    assert result.returncode == 0

    output_dir, options_path = prepare_benders_runtime_files(study_root)

    benders_bin = get_antares_xpansion_benders_bin(current_dir)
    benders_start = time.perf_counter()
    result = subprocess.run(
        [str(benders_bin), str(options_path.name)],
        capture_output=True,
        text=True,
        check=False,
        cwd=str(output_dir),
    )
    benders_elapsed = time.perf_counter() - benders_start
    logger.info("==============================================")
    logger.info("Benders output:")
    logger.info("elapsed_seconds: %s", benders_elapsed)
    logger.info("returncode: %s", result.returncode)
    logger.info("stdout: %s", result.stdout)
    logger.info("stderr: %s", result.stderr)
    logger.info("==============================================")
    assert result.returncode == 0

    xpansion_result = json.loads((output_dir / "expansion" / "out.json").read_text(encoding="utf-8"))
    xpansion_solution = xpansion_result["solution"]
    pypsa_total_objective = _get_pypsa_total_objective(pypsa_network)

    logger.info("==============================================")
    logger.info("Objective comparison:")
    logger.info("pypsa_total_objective: %s", pypsa_total_objective)
    logger.info("xpansion_overall_cost: %s", xpansion_solution["overall_cost"])
    logger.info("xpansion_runtime_seconds: %s", xpansion_result["run_duration"])
    logger.info("==============================================")

    assert xpansion_solution["problem_status"] == "OPTIMAL"
    assert xpansion_solution["overall_cost"] == pytest.approx(pypsa_total_objective)
    assert xpansion_solution["values"]["generator_gen1.p_nom"] == pytest.approx(
        pypsa_network.generators.loc[("low", "gen1"), "p_nom_opt"]
    )
    assert xpansion_solution["values"]["generator_gen2.p_nom"] == pytest.approx(
        pypsa_network.generators.loc[("low", "gen2"), "p_nom_opt"]
    )
