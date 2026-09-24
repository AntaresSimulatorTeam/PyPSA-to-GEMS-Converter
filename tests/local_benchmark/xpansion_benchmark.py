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
Benchmarks Antares-Xpansion (Benders decomposition) against PyPSA's monolithic solve on
synthetic investment studies, scaled across network size and number of scenarios.

The GEMS study is PyPSAStudyConverter(full_gems=True): no legacy virtual-area hybrid study
and no Xpansion-launcher inputs. Execution is still Antares-Xpansion: GemsPy's
gems_runner.study.runner.run_study() writes the Benders subproblems (one per scenario and
week-block) and shells out to the Xpansion 1.9.0 `bin/benders` binary. Both sides use Coin.

Networks are built in code (no .nc fixtures): a connected AC ring with one extendable
generator and one load per bus, then fanned out into equal-weight scenarios with
independently perturbed loads.

Run with, e.g.: pytest tests/local_benchmark/xpansion_benchmark.py -s
Results are appended to tmp/xpansion_benchmark_results/xpansion_scenario_results.csv.
"""

import hashlib
import logging
import os
import shutil
import time
from pathlib import Path

import numpy as np
import pandas as pd
import pytest
from gems_runner.study.runner import run_study
from pypsa import Network

from src.dependencies import get_antares_xpansion_dir, get_antares_xpansion_dir_name, get_antares_xpansion_version
from src.pypsa_converter import PyPSAStudyConverter
from src.utils import read_mps_problem_size, read_xpansion_out_json
from tests.utils import PROJECT_ROOT, get_gemspy_version

# PyPSA's Cbc and Xpansion's COIN are the same Coin LP solver. GemsPy writes
# SOLVER_NAME=COIN in options.json.
PYPSA_SOLVER_NAME = "cbc"
GEMS_SOLVER_NAME = "coin"
XPANSION_SOLVER_NAME = "COIN"

logger = logging.getLogger("xpansion_benchmark")
logger.setLevel(logging.INFO)

# (n_buses, n_timesteps, study_name) — sized so each operational subproblem is nontrivial
# and scenario scaling can expose Benders' advantage over a monolithic PyPSA LP.
STUDIES = [
    (20, 168, "synthetic_mesh_20x168"),
    (40, 168, "synthetic_mesh_40x168"),
    (40, 672, "synthetic_mesh_40x672"),
]

# Scenario counts start at 2 so Benders faces multiple distinct operational subproblems.
# (A single-scenario investment study is still Xpansion-runnable, but uninteresting for
# this scaling benchmark.)
SCENARIO_COUNTS = [2, 10, 50]


def _seed_for(study_name: str, n_scenarios: int) -> int:
    """Stable (PYTHONHASHSEED-independent) seed so re-runs perturb scenarios identically."""
    digest = hashlib.sha256(f"{study_name}_{n_scenarios}".encode()).digest()
    return int.from_bytes(digest[:4], "big")


# Worst-case load scale used both for scenario perturbation and to size p_nom_min so the
# first Benders master iterate (all candidates at lower bound) stays feasible. Xpansion 1.9 /
# Clp aborts on infeasible subproblems instead of recovering with feasibility cuts.
_LOAD_NOISE_LOW = 0.85
_LOAD_NOISE_HIGH = 1.15
_GEN_P_MAX_PU = 0.95


def add_perturbed_scenarios(network: Network, n_scenarios: int, seed: int) -> Network:
    """
    Fan a single-scenario network into n_scenarios equal-weight scenarios.

    Each scenario scenario_i gets weight 1/n and its load time series
    (loads_t.p_set) is multiplied by an independent factor drawn uniformly from
    [_LOAD_NOISE_LOW, _LOAD_NOISE_HIGH] = [0.85, 1.15]. The same factor is applied
    to every bus in that scenario, so relative load shape across buses is preserved
    while absolute demand (and therefore the operational optimum) differs by year.

    Generators p_max_pu and line ratings are left unchanged across scenarios.
    """
    rng = np.random.default_rng(seed)
    scenario_names = [f"scenario_{i}" for i in range(n_scenarios)]
    weight = 1.0 / n_scenarios
    network.set_scenarios(dict.fromkeys(scenario_names, weight))

    factors = rng.uniform(_LOAD_NOISE_LOW, _LOAD_NOISE_HIGH, size=n_scenarios)
    for scenario_name, factor in zip(scenario_names, factors):
        network.loads_t.p_set[scenario_name] = network.loads_t.p_set[scenario_name] * factor
    return network


def build_synthetic_investment_network(n_buses: int, n_timesteps: int, *, name: str) -> Network:
    """
    Connected AC-ring investment network used as the Xpansion vs PyPSA benchmark.

    Topology
      n_buses buses on a ring of fixed AC lines (s_nom=500, not extendable).
      Every bus bus_i has one local load load_i and one extendable generator
      gen_i. No Links / storage — Slack theta stays in the angle matrix so Clp
      can read the MPS.

    Loads (deterministic before scenario perturbation)
      Bus i base demand 50 + 10*(i % 5) MW (cycles every 5 buses: 50…90), plus a
      daily swing of amplitude 25 MW phase-shifted by 3*i hours:
      p(t) = base + 25 * (((t + 3*i) % 24) / 23).
      So neighbouring buses peak at different hours; the ring must carry some transfer.

    Generators (all p_nom_extendable, p_max_pu=0.95)
      Lower bound sized for feasibility under the worst load factor 1.15:
      p_nom_min = (base+25) * 1.15 / 0.95 * 1.05, p_nom_max = 3 * p_nom_min.
      Cost gradient with bus index (keeps expansion nontrivial):
        - cheap OPEX / cheap CAPEX at low i: c_marg = 20 + 5*i, c_cap = 200 + 50*i
        - expensive OPEX / expensive CAPEX at high i
      So the optimizer prefers investing more on low-index gens and transferring power
      over the ring rather than building expensive local capacity everywhere.

    Scenarios
      Added afterwards by add_perturbed_scenarios (equal weights, independent load
      scale factors). Horizon must be a multiple of 168 h for the hybrid Xpansion study.
    """
    if n_buses < 2:
        raise ValueError(f"n_buses must be >= 2, got {n_buses}")
    if n_timesteps < 1:
        raise ValueError(f"n_timesteps must be >= 1, got {n_timesteps}")

    network = Network(name=name, snapshots=range(n_timesteps))
    network.snapshot_weightings.loc[:] = 1.0
    network.add("Carrier", "AC", co2_emissions=0)

    for i in range(n_buses):
        network.add("Bus", f"bus_{i}", v_nom=220, carrier="AC")

    for i in range(n_buses):
        base_load = 50.0 + 10.0 * (i % 5)
        load_swing = 25.0
        p_set = [base_load + load_swing * (((t + 3 * i) % 24) / 23.0) for t in range(n_timesteps)]
        network.add("Load", f"load_{i}", bus=f"bus_{i}", p_set=p_set, q_set=0)

        # Local min capacity covers peak load after max scenario perturbation (plus margin).
        peak_load = base_load + load_swing
        p_nom = peak_load * _LOAD_NOISE_HIGH / _GEN_P_MAX_PU * 1.05
        network.add(
            "Generator",
            f"gen_{i}",
            bus=f"bus_{i}",
            p_nom_extendable=True,
            p_nom=p_nom,
            p_nom_min=p_nom,
            p_nom_max=p_nom * 3.0,
            # Cheap gens (low i) invite expansion for lower OPEX — keeps the investment LP nontrivial.
            marginal_cost=20.0 + 5.0 * i,
            capital_cost=200.0 + 50.0 * i,
            p_max_pu=[_GEN_P_MAX_PU] * n_timesteps,
        )

    # Ring of AC lines → one connected AC component (Slack theta appears in COLUMNS for Clp).
    for i in range(n_buses):
        j = (i + 1) % n_buses
        network.add(
            "Line",
            f"line_{i}_{j}",
            bus0=f"bus_{i}",
            bus1=f"bus_{j}",
            x=0.1,
            r=0.01,
            s_nom=500.0,
            s_nom_extendable=False,
        )

    return network


def _run_full_gems_benders(full_gems_study_dir: Path, xpansion_root: Path) -> tuple[str, float | None, float]:
    """Convert-and-run a full-GEMS investment study via gems_runner.study.runner.run_study().

    gems_runner.simulation.runner.BendersRunner shells out to a hardcoded 'bin/benders' path
    resolved relative to the process's cwd at call time (see GemsPy's runner.py), so we chdir
    into the Xpansion 1.9.0 install root — whose bin/ holds that binary — then restore cwd.

    Returns (status, objective_value_or_None, elapsed_seconds). Never raises: a failure is
    recorded as a "FAILED"/"NO_OUTPUT" row rather than aborting the whole benchmark.
    """
    gems_root = full_gems_study_dir / "systems"
    previous_cwd = Path.cwd()
    start = time.time()
    try:
        os.chdir(xpansion_root)
        run_study(gems_root)
    except Exception:
        elapsed = time.time() - start
        logger.exception("gems_runner.study.runner.run_study failed on %s", gems_root)
        return "FAILED", None, elapsed
    finally:
        os.chdir(previous_cwd)
    elapsed = time.time() - start

    try:
        solution = read_xpansion_out_json(gems_root)["solution"]
        return str(solution["problem_status"]), float(solution["overall_cost"]), elapsed
    except (FileNotFoundError, KeyError) as exc:
        logger.warning("run_study() completed but no readable out.json under %s: %s", gems_root, exc)
        return "NO_OUTPUT", None, elapsed


@pytest.mark.parametrize("n_buses, n_timesteps, study_name", STUDIES)
@pytest.mark.parametrize("n_scenarios", SCENARIO_COUNTS)
def test_xpansion_vs_pypsa_scenario_scaling(n_buses: int, n_timesteps: int, study_name: str, n_scenarios: int) -> None:
    if not (PROJECT_ROOT / get_antares_xpansion_dir_name()).is_dir():
        pytest.skip(
            f"Antares Xpansion binaries not found. Please download version {get_antares_xpansion_version()} "
            "from https://github.com/AntaresSimulatorTeam/antares-xpansion/releases"
        )

    run_name = f"{study_name}_n{n_scenarios}"
    logger.info("Running Xpansion-vs-PyPSA benchmark for: %s", run_name)
    benchmark_data_frame = pd.DataFrame()
    benchmark_data_frame.loc[0, "study_name"] = study_name
    benchmark_data_frame.loc[0, "n_buses"] = n_buses
    benchmark_data_frame.loc[0, "n_scenarios"] = n_scenarios
    benchmark_data_frame.loc[0, "antares_xpansion_version"] = f"v{get_antares_xpansion_version()}"
    benchmark_data_frame.loc[0, "xpansion_solver_name"] = XPANSION_SOLVER_NAME
    benchmark_data_frame.loc[0, "gemspy_version"] = get_gemspy_version()

    # ==================================================================================
    # PyPSA: build a synthetic investment network, then fan it into n_scenarios with
    # perturbed loads so Benders subproblems differ.
    # ==================================================================================
    build_start = time.time()
    network = build_synthetic_investment_network(n_buses, n_timesteps, name=study_name)
    network = add_perturbed_scenarios(network, n_scenarios, seed=_seed_for(study_name, n_scenarios))
    build_elapsed = time.time() - build_start
    benchmark_data_frame.loc[0, "pypsa_network_name"] = network.name
    benchmark_data_frame.loc[0, "number_of_time_steps"] = len(network.snapshots)
    benchmark_data_frame.loc[0, "network_build_time"] = build_elapsed

    # ==================================================================================
    # Full-GEMS: no legacy virtual-area hybrid study. run_study() does the
    # per-(scenario, week-block) Benders split.
    # ==================================================================================
    study_dir = PROJECT_ROOT / "tmp" / f"{run_name}_full_gems"
    logger.info("Converting PyPSA network to a full-GEMS study (no legacy virtual area)")
    start_full_gems_conversion = time.time()
    PyPSAStudyConverter(
        pypsa_network=network,
        study_dir=study_dir,
        series_file_format=".tsv",
        solver_name=GEMS_SOLVER_NAME,
        full_gems=True,
    ).to_gems_study()
    benchmark_data_frame.loc[0, "full_gems_conversion_time"] = time.time() - start_full_gems_conversion

    logger.info("Running gems_runner.study.runner.run_study on %s", study_dir / "systems")
    full_gems_status, full_gems_objective, full_gems_elapsed = _run_full_gems_benders(
        study_dir, get_antares_xpansion_dir(PROJECT_ROOT)
    )
    benchmark_data_frame.loc[0, "full_gems_status"] = full_gems_status
    benchmark_data_frame.loc[0, "full_gems_objective_value"] = full_gems_objective
    benchmark_data_frame.loc[0, "full_gems_total_time"] = full_gems_elapsed
    try:
        output_dirs = sorted((study_dir / "systems" / "output").iterdir())
        mps_dir = output_dirs[-1]
        master_vars, master_cons = read_mps_problem_size(mps_dir / "master.mps")
        subproblems = sorted(mps_dir.glob("subproblem_*.mps"))
        sub_vars, sub_cons = read_mps_problem_size(subproblems[0])
        n_subproblems = len(subproblems)
        benchmark_data_frame.loc[0, "number_of_full_gems_subproblems"] = n_subproblems
        benchmark_data_frame.loc[0, "number_of_constraints_full_gems_master"] = master_cons
        benchmark_data_frame.loc[0, "number_of_variables_full_gems_master"] = master_vars
        benchmark_data_frame.loc[0, "number_of_constraints_full_gems_subproblem"] = sub_cons
        benchmark_data_frame.loc[0, "number_of_variables_full_gems_subproblem"] = sub_vars
        benchmark_data_frame.loc[0, "number_of_constraints_full_gems"] = master_cons + n_subproblems * sub_cons
        benchmark_data_frame.loc[0, "number_of_variables_full_gems"] = master_vars + n_subproblems * (
            sub_vars - master_vars
        )
        logger.info(
            "Full-GEMS MPS sizes: %s constraints (%s master + %s× %s), %s variables",
            int(benchmark_data_frame.loc[0, "number_of_constraints_full_gems"]),
            master_cons,
            n_subproblems,
            sub_cons,
            int(benchmark_data_frame.loc[0, "number_of_variables_full_gems"]),
        )
    except (FileNotFoundError, IndexError, ValueError, StopIteration) as exc:
        logger.warning("Could not read full-GEMS MPS sizes: %s", exc)

    # ==================================================================================
    # PyPSA: build and solve the same investment problem as one monolithic LP
    # ==================================================================================
    logger.info("Building PyPSA optimization problem")
    start_time_build = time.time()
    network.optimize.create_model()
    pypsa_build_time = time.time() - start_time_build
    benchmark_data_frame.loc[0, "pypsa_build_time"] = pypsa_build_time

    logger.info("Solving PyPSA optimization problem")
    start_time_solve = time.time()
    network.optimize.solve_model(solver_name=PYPSA_SOLVER_NAME)
    pypsa_solve_time = time.time() - start_time_solve
    benchmark_data_frame.loc[0, "pypsa_solve_time"] = pypsa_solve_time
    benchmark_data_frame.loc[0, "pypsa_total_time"] = pypsa_build_time + pypsa_solve_time

    benchmark_data_frame.loc[0, "number_of_constraints_pypsa"] = network.model.ncons
    benchmark_data_frame.loc[0, "number_of_variables_pypsa"] = network.model.nvars
    assert network.objective is not None
    assert network.objective_constant is not None
    benchmark_data_frame.loc[0, "pypsa_objective"] = network.objective + network.objective_constant
    benchmark_data_frame.loc[0, "pypsa_solver_name"] = network.model.solver_name

    # ==================================================================================
    # Results: append one row to the combined benchmark CSV
    # ==================================================================================
    results_dir = PROJECT_ROOT / "tmp" / "xpansion_benchmark_results"
    results_dir.mkdir(parents=True, exist_ok=True)
    combined_results_file = results_dir / "xpansion_scenario_results.csv"

    if combined_results_file.exists():
        existing = pd.read_csv(combined_results_file)
        pd.concat([existing, benchmark_data_frame], ignore_index=True).to_csv(combined_results_file, index=False)
    else:
        benchmark_data_frame.to_csv(combined_results_file, index=False)
    logger.info("Appended benchmark results to %s", combined_results_file)

    shutil.rmtree(study_dir, ignore_errors=True)
