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


def _run_hybrid_solver(antares_study_dir: Path) -> list[Path]:
    """
    Step 6 of the trick (see src/antares_hybrid_writer.py's module docstring): run
    antares-solver with no mode flag (generaldata.ini defaults to Economy; there is
    nothing to expand) and return every simulation_table CSV under output/.

    Antares emit more than one table (e.g. simulation_table--optim-nb-1/2.csv).
    Callers must read them all when building the scenario-weighted objective.
    """
    solver_bin = get_antares_solver_bin(PROJECT_ROOT)
    if antares_study_dir.joinpath("output").exists():
        shutil.rmtree(antares_study_dir / "output")

    completed = subprocess.run(
        [str(solver_bin), "-i", str(antares_study_dir.resolve())],
        capture_output=True,
        text=True,
        check=False,
        cwd=str(solver_bin.parent),
    )

    result_files = list(antares_study_dir.glob("output/**/simulation_table*"))
    if not result_files:
        raise FileNotFoundError(
            f"No simulation_table found under {antares_study_dir / 'output'}; "
            "antares-solver likely failed to load the hybrid study.\n"
            f"exit_code={completed.returncode}\n"
            f"stdout:\n{completed.stdout}\n"
            f"stderr:\n{completed.stderr}"
        )
    return result_files


def _weighted_objective(simulation_tables: list[Path], scenario_weights: list[float]) -> float:
    """
    Scenario-weighted objective from all antares-solver simulation tables.

    Each table is read; OBJECTIVE_VALUE
    rows are collected per scenario_index (duplicate tables / optim-nb passes must
    agree); then the weighted average over scenarios is returned.
    """
    frames = [pd.read_csv(path, usecols=["output", "scenario_index", "value"]) for path in simulation_tables]
    objectives = (
        pd.concat(frames, ignore_index=True)
        .query("output == 'OBJECTIVE_VALUE'")
        .groupby("scenario_index", sort=True)["value"]
        .agg(["min", "max", "first"])
    )
    inconsistent = objectives.index[objectives["min"] != objectives["max"]].tolist()
    if inconsistent:
        raise AssertionError(
            f"Conflicting OBJECTIVE_VALUE across simulation tables for scenario_index={inconsistent}: "
            f"{objectives.loc[inconsistent].to_dict()}"
        )
    if list(objectives.index) != list(range(len(scenario_weights))):
        raise AssertionError(
            f"Expected OBJECTIVE_VALUE for scenario_index 0..{len(scenario_weights) - 1}, "
            f"got {list(objectives.index)} from {[p.name for p in simulation_tables]}"
        )
    per_scenario = objectives["first"]
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


def _build_hybrid_network(
    name: str,
    scenarios: dict[str, float],
    *,
    load_p_set: list[float] | None = None,
    gen2_p_nom: float = 50,
    gen2_p_max_pu: list[float] | None = None,
    scenario_load_p_set: dict[str, list[float]] | None = None,
    scenario_gen2_p_max_pu: dict[str, list[float]] | None = None,
) -> Network:
    """One-bus town: expensive always-available gen1 plus cheap, availability-limited gen2."""
    hours = list(range(HOURS_PER_WEEK))
    if load_p_set is None:
        load_p_set = [80 + (h % 24) for h in hours]
    if gen2_p_max_pu is None:
        gen2_p_max_pu = [0.9] * HOURS_PER_WEEK

    network = Network(name=name, snapshots=hours)
    network.add("Bus", "town", v_nom=1)
    network.add("Load", "load1", bus="town", p_set=load_p_set, q_set=0)
    network.add("Generator", "gen1", bus="town", p_nom_extendable=False, marginal_cost=50, p_nom=200)
    network.add(
        "Generator",
        "gen2",
        bus="town",
        p_nom_extendable=False,
        marginal_cost=10,
        p_nom=gen2_p_nom,
        p_max_pu=gen2_p_max_pu,
    )
    network.set_scenarios(scenarios)
    if scenario_load_p_set is not None:
        for scenario, series in scenario_load_p_set.items():
            network.loads_t.p_set[(scenario, "load1")] = series
    if scenario_gen2_p_max_pu is not None:
        for scenario, series in scenario_gen2_p_max_pu.items():
            network.generators_t.p_max_pu[(scenario, "gen2")] = series
    return network


def _assert_hybrid_matches_pypsa_and_gemspy(
    network: Network,
    gems_dir: Path,
    scenario_weights: list[float],
) -> None:
    PyPSAStudyConverter(pypsa_network=network, study_dir=gems_dir, series_file_format=".tsv").to_gems_study()

    network.optimize()
    pypsa_objective = network.objective + network.objective_constant

    gemspy_objective = _gemspy_objective(gems_dir / "systems", n_scenarios=len(scenario_weights))
    antares_study_dir = gems_dir / network.name
    assert antares_study_dir.is_dir(), (
        "to_gems_study() should have auto-built the antares-solver hybrid study "
        "for this multi-scenario, non-investment network by default"
    )
    hybrid_objective = _weighted_objective(_run_hybrid_solver(antares_study_dir), scenario_weights=scenario_weights)

    assert math.isclose(pypsa_objective, gemspy_objective, rel_tol=1e-6)
    assert math.isclose(pypsa_objective, hybrid_objective, rel_tol=1e-6)


@pytest.mark.parametrize("n_scenarios", [2, 3, 6])
def test_hybrid_n_scenarios_matches_pypsa_and_gemspy(n_scenarios: int) -> None:
    """Hybrid cross-check parametrized for 2, 3 and 6 equal-weight scenarios
    (GemsPy currently requires equal weights)."""
    weight = 1.0 / n_scenarios
    scenario_weights = [weight] * n_scenarios
    # Distinct per-scenario availability so the scenario axis is exercised.
    scenario_gen2_p_max_pu = {f"s{i}": [1.0 - 0.8 * i / (n_scenarios - 1)] * HOURS_PER_WEEK for i in range(n_scenarios)}
    network = _build_hybrid_network(
        f"Hybrid{n_scenarios}Scenarios",
        {f"s{i}": weight for i in range(n_scenarios)},
        scenario_gen2_p_max_pu=scenario_gen2_p_max_pu,
    )
    _assert_hybrid_matches_pypsa_and_gemspy(
        network,
        PROJECT_ROOT / "tmp" / f"test_hybrid_{n_scenarios}_scenarios",
        scenario_weights,
    )


def test_hybrid_distinct_scenario_timeseries_matches_pypsa_and_gemspy() -> None:
    """
    Multi-scenario hybrid check where each scenario has a *different* time-series
    shape (not just a different constant availability): load profiles and gen2
    availability patterns are anti-correlated across scenarios so that mixing up
    MC-year data-series columns would change the objective.
    """
    hours = list(range(HOURS_PER_WEEK))
    # Scenario "day": daytime-heavy load, cheap gen available only in daytime.
    # Scenario "night": nighttime-heavy load, cheap gen available only at night.
    day_load = [120.0 if 8 <= (h % 24) < 20 else 40.0 for h in hours]
    night_load = [40.0 if 8 <= (h % 24) < 20 else 120.0 for h in hours]
    day_availability = [1.0 if 8 <= (h % 24) < 20 else 0.0 for h in hours]
    night_availability = [0.0 if 8 <= (h % 24) < 20 else 1.0 for h in hours]

    network = _build_hybrid_network(
        "HybridDistinctTimeseries",
        {"day": 0.5, "night": 0.5},
        load_p_set=day_load,
        gen2_p_nom=80,
        gen2_p_max_pu=[0.5] * HOURS_PER_WEEK,
        scenario_load_p_set={"day": day_load, "night": night_load},
        scenario_gen2_p_max_pu={"day": day_availability, "night": night_availability},
    )
    _assert_hybrid_matches_pypsa_and_gemspy(
        network,
        PROJECT_ROOT / "tmp" / "test_hybrid_distinct_scenario_timeseries",
        [0.5, 0.5],
    )


def test_unequal_scenario_weights_are_rejected() -> None:
    """
    Multi-scenario studies with UNEQUAL scenario weights (0.1 / 0.9) must be
    rejected at conversion time with a clear error, rather than silently producing a
    GEMS study whose GemsPy/antares-modeler results don't match PyPSA's.
    - pypsa's true weighted objective:  0.1 * 432600 + 0.9 * 701400 = 674520.0
    - gemspy's unweighted average:       (432600 + 701400) / 2      = 567000.0
    """
    network = _build_hybrid_network(
        "HybridUnequalWeights",
        {"low": 0.1, "high": 0.9},
        scenario_gen2_p_max_pu={
            "low": [1.0] * HOURS_PER_WEEK,
            "high": [0.2] * HOURS_PER_WEEK,
        },
    )
    with pytest.raises(ValueError, match="same weight"):
        PyPSAStudyConverter(
            pypsa_network=network,
            study_dir=PROJECT_ROOT / "tmp" / "test_unequal_scenario_weights",
            series_file_format=".tsv",
        )
