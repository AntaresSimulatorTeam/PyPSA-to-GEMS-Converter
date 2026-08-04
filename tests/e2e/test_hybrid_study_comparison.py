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

"""
Cross-checks PyPSA / GemsPy / Antares Modeler / Antares Solver on the same converted
GEMS study, using the antares-solver hybrid-study trick implemented in
`src/antares_hybrid_writer.py` (see that module's docstring for the full rationale:
why antares-modeler alone can't solve more than scenario 0, why a bare classic Antares
study with one inert virtual area unlocks antares-solver's Monte-Carlo-year loop, and
why optim-config.yml/system.yml/modeler-scenariobuilder.dat need patching).

`PyPSAStudyConverter.to_gems_study()` now builds this hybrid study automatically for
any multi-scenario, non-investment study (see `_has_extendable_capacity` there) --
`test_hybrid_two_scenarios_matches_pypsa_and_gemspy` and the parametrized
`test_hybrid_n_scenarios_matches_pypsa_and_gemspy` (3 / 6 scenarios) exercise that
automatic path.
"""

import math
import shutil
import subprocess
from pathlib import Path

import pandas as pd
import pytest
from gems_craft.optim_config.parsing import OptimConfig, validate_optim_config
from gems_craft.study.folder import load_study
from gems_runner.simulation.optimization import build_problem
from gems_runner.simulation.time_block import TimeBlock
from pypsa import Network

from src.antares_hybrid_writer import AntaresHybridStudyWriter
from src.dependencies import get_antares_dir_name, get_antares_solver_bin
from src.pypsa_converter import PyPSAStudyConverter

PROJECT_ROOT = Path(__file__).resolve().parents[2]

HOURS_PER_WEEK = 168


@pytest.fixture(autouse=True)
def check_hybrid_prerequisites() -> None:
    """Skip (rather than fail) when the Antares binaries or antares-craft aren't available."""
    if not (PROJECT_ROOT / get_antares_dir_name()).is_dir():
        pytest.skip(
            f"Antares binaries not found. Please download version from "
            f"https://github.com/AntaresSimulatorTeam/Antares_Simulator/releases into {PROJECT_ROOT}"
        )


def _run_hybrid_solver(antares_study_dir: Path) -> Path:
    """
    Step 6 of the trick (see src/antares_hybrid_writer.py's module docstring): run
    antares-solver with no mode flag (generaldata.ini defaults to Economy; there is
    nothing to expand) and return the resulting simulation_table CSV.
    """
    solver_bin = get_antares_solver_bin(PROJECT_ROOT)
    if antares_study_dir.joinpath("output").exists():
        shutil.rmtree(antares_study_dir / "output")

    subprocess.run(
        [str(solver_bin), "-i", str(antares_study_dir)],
        capture_output=True,
        text=True,
        check=False,
        cwd=str(solver_bin.parent),
    )

    result_file = next(antares_study_dir.glob("output/**/simulation_table*"), None)
    if result_file is None:
        raise FileNotFoundError(
            f"No simulation_table found under {antares_study_dir / 'output'}; "
            "antares-solver likely failed to load the hybrid study, see stdout/stderr above."
        )
    return result_file


def _weighted_objective(simulation_table: Path, scenario_weights: list[float]) -> float:
    """
    tests.utils.get_objective_value only reads the *first* OBJECTIVE_VALUE row,
    which is fine for single-scenario runs but not for multi-scenario ones: antares-solver
    writes one OBJECTIVE_VALUE row per MC year/scenario_index, and they must be
    combined the same way PyPSA/GemsPy combine them (weighted average).
    """
    df = pd.read_csv(simulation_table, usecols=["output", "scenario_index", "value"])
    per_scenario = df.query("output == 'OBJECTIVE_VALUE'").set_index("scenario_index")["value"]
    return float(sum(per_scenario[i] * scenario_weights[i] for i in range(len(scenario_weights))))


def _gemspy_objective(gems_systems_dir: Path, n_scenarios: int) -> float:
    gemspy_study = load_study(gems_systems_dir)
    optim_config = OptimConfig()
    optim_config.time_scope.first_time_step = 0
    optim_config.time_scope.last_time_step = HOURS_PER_WEEK - 1
    optim_config.scenario_scope.include = list(range(n_scenarios))
    validate_optim_config(optim_config, gemspy_study.system)

    timesteps = list(range(HOURS_PER_WEEK))
    block = TimeBlock(0, timesteps)
    scenario_ids = optim_config.scenario_scope.scenario_ids
    problem = build_problem(gemspy_study, block, scenario_ids, optim_config=optim_config)
    problem.solve(solver_name=optim_config.solver_options.name, **optim_config.solver_options.parsed_parameters())
    return float(problem.objective_value)


def test_hybrid_two_scenarios_matches_pypsa_and_gemspy() -> None:
    """
    Two-scenario (stochastic) network: pypsa / gemspy / antares-solver (hybrid
    trick) must agree on the scenario-weighted objective.

    Unlike the deterministic baseline above, this study IS multi-scenario and
    non-investment, so `PyPSAStudyConverter.to_gems_study()` builds the hybrid study
    automatically -- this test uses that auto-built study directly (no manual
    `AntaresHybridStudyWriter` call), proving the production wiring itself works.

    antares-modeler alone is deliberately NOT asserted equal here: invoked directly
    (without the hybrid trick) it only ever solves one scenario -- this is exactly
    the multi-scenario gap the hybrid trick exists to validate (see
    src/antares_hybrid_writer.py's module docstring). It is still computed and
    printed for visibility.
    """
    study_name = "test_hybrid_two_scenarios"
    gems_dir = PROJECT_ROOT / "tmp" / study_name
    scenario_weights = [0.5, 0.5]

    network = Network(name="HybridTwoScenarios", snapshots=list(range(HOURS_PER_WEEK)))
    network.add("Bus", "town", v_nom=1)
    network.add("Load", "load1", bus="town", p_set=[80 + (i % 24) for i in range(HOURS_PER_WEEK)], q_set=0)
    network.add("Generator", "gen1", bus="town", p_nom_extendable=False, marginal_cost=50, p_nom=200)
    network.add(
        "Generator",
        "gen2",
        bus="town",
        p_nom_extendable=False,
        marginal_cost=10,
        p_nom=50,
        p_max_pu=[0.9] * HOURS_PER_WEEK,
    )
    network.set_scenarios({"low": 0.5, "high": 0.5})
    # Static (non-time-series) per-scenario overrides are collapsed to a single value
    # by the converter today (see gems_model_builder.py's first_per_component logic),
    # so the scenario axis must be exercised through a genuinely time-varying
    # attribute: gen2's availability differs per scenario.
    network.generators_t.p_max_pu[("low", "gen2")] = [1.0] * HOURS_PER_WEEK
    network.generators_t.p_max_pu[("high", "gen2")] = [0.2] * HOURS_PER_WEEK

    PyPSAStudyConverter(pypsa_network=network, study_dir=gems_dir, series_file_format=".tsv").to_gems_study()
    gems_systems_dir = gems_dir / "systems"

    network.optimize()
    pypsa_objective = network.objective + network.objective_constant

    gemspy_objective = _gemspy_objective(gems_systems_dir, n_scenarios=2)

    antares_study_dir = gems_dir / AntaresHybridStudyWriter.study_name
    assert antares_study_dir.is_dir(), (
        "to_gems_study() should have auto-built the antares-solver hybrid study "
        "for this multi-scenario, non-investment network by default"
    )
    hybrid_result_file = _run_hybrid_solver(antares_study_dir)
    hybrid_objective = _weighted_objective(hybrid_result_file, scenario_weights=scenario_weights)

    assert math.isclose(pypsa_objective, gemspy_objective, rel_tol=1e-6)
    assert math.isclose(pypsa_objective, hybrid_objective, rel_tol=1e-6)


@pytest.mark.parametrize("n_scenarios", [3, 6])
def test_hybrid_n_scenarios_matches_pypsa_and_gemspy(n_scenarios: int) -> None:
    """Same hybrid cross-check as the two-scenario test, parametrized for 3 and 6
    equal-weight scenarios (GemsPy currently requires equal weights)."""
    study_name = f"test_hybrid_{n_scenarios}_scenarios"
    gems_dir = PROJECT_ROOT / "tmp" / study_name
    weight = 1.0 / n_scenarios
    scenario_weights = [weight] * n_scenarios
    scenarios = {f"s{i}": weight for i in range(n_scenarios)}

    network = Network(name=f"Hybrid{n_scenarios}Scenarios", snapshots=list(range(HOURS_PER_WEEK)))
    network.add("Bus", "town", v_nom=1)
    network.add("Load", "load1", bus="town", p_set=[80 + (i % 24) for i in range(HOURS_PER_WEEK)], q_set=0)
    network.add("Generator", "gen1", bus="town", p_nom_extendable=False, marginal_cost=50, p_nom=200)
    network.add(
        "Generator",
        "gen2",
        bus="town",
        p_nom_extendable=False,
        marginal_cost=10,
        p_nom=50,
        p_max_pu=[0.9] * HOURS_PER_WEEK,
    )
    network.set_scenarios(scenarios)
    # Distinct per-scenario availability so the scenario axis is exercised (see
    # comment in test_hybrid_two_scenarios_matches_pypsa_and_gemspy).
    for i in range(n_scenarios):
        availability = 1.0 - 0.8 * i / (n_scenarios - 1)
        network.generators_t.p_max_pu[(f"s{i}", "gen2")] = [availability] * HOURS_PER_WEEK

    PyPSAStudyConverter(pypsa_network=network, study_dir=gems_dir, series_file_format=".tsv").to_gems_study()
    gems_systems_dir = gems_dir / "systems"

    network.optimize()
    pypsa_objective = network.objective + network.objective_constant

    gemspy_objective = _gemspy_objective(gems_systems_dir, n_scenarios=n_scenarios)

    antares_study_dir = gems_dir / AntaresHybridStudyWriter.study_name
    assert antares_study_dir.is_dir(), (
        "to_gems_study() should have auto-built the antares-solver hybrid study "
        "for this multi-scenario, non-investment network by default"
    )
    hybrid_result_file = _run_hybrid_solver(antares_study_dir)
    hybrid_objective = _weighted_objective(hybrid_result_file, scenario_weights=scenario_weights)

    assert math.isclose(pypsa_objective, gemspy_objective, rel_tol=1e-6)
    assert math.isclose(pypsa_objective, hybrid_objective, rel_tol=1e-6)


def test_unequal_scenario_weights_are_rejected() -> None:
    """
    Multi-scenario studies with UNEQUAL scenario weights (0.1 / 0.9) must be
    rejected at conversion time with a clear error, rather than silently producing a
    GEMS study whose GemsPy/antares-modeler results don't match PyPSA's.
    - pypsa's true weighted objective:  0.1 * 432600 + 0.9 * 701400 = 674520.0
    - gemspy's unweighted average:       (432600 + 701400) / 2      = 567000.0
    """
    study_name = "test_unequal_scenario_weights"
    gems_dir = PROJECT_ROOT / "tmp" / study_name

    network = Network(name="HybridUnequalWeights", snapshots=list(range(HOURS_PER_WEEK)))
    network.add("Bus", "town", v_nom=1)
    network.add("Load", "load1", bus="town", p_set=[80 + (i % 24) for i in range(HOURS_PER_WEEK)], q_set=0)
    network.add("Generator", "gen1", bus="town", p_nom_extendable=False, marginal_cost=50, p_nom=200)
    network.add(
        "Generator",
        "gen2",
        bus="town",
        p_nom_extendable=False,
        marginal_cost=10,
        p_nom=50,
        p_max_pu=[0.9] * HOURS_PER_WEEK,
    )
    network.set_scenarios({"low": 0.1, "high": 0.9})
    network.generators_t.p_max_pu[("low", "gen2")] = [1.0] * HOURS_PER_WEEK
    network.generators_t.p_max_pu[("high", "gen2")] = [0.2] * HOURS_PER_WEEK

    with pytest.raises(ValueError, match="same weight"):
        PyPSAStudyConverter(pypsa_network=network, study_dir=gems_dir, series_file_format=".tsv")
