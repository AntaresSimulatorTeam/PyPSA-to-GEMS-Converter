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

Networks are built in code (no .nc fixtures): a connected AC ring with one extendable
generator and one load per bus, then fanned out into equal-weight scenarios with
independently perturbed loads. That keeps the study on the antares-xpansion-launcher
GEMS path and gives Benders genuinely different subproblems to exploit versus PyPSA
solving one big block.

Run with, e.g.: pytest tests/local_benchmark/xpansion_benchmark.py -s
Results are appended to tmp/xpansion_benchmark_results/xpansion_scenario_results.csv.
"""

import hashlib
import logging
import shutil
import time

import numpy as np
import pandas as pd
import pytest
from pypsa import Network

from src.dependencies import (
    get_antares_dir_name,
    get_antares_version,
    get_antares_xpansion_dir_name,
    get_antares_xpansion_launcher_bin,
    get_antares_xpansion_version,
)
from src.pypsa_converter import PyPSAStudyConverter
from src.utils import read_xpansion_out_json, run_xpansion_launcher
from tests.utils import PROJECT_ROOT, get_gemspy_version

# PyPSA is solved with the same underlying solver ('coin'/Cbc) that the GEMS side (Antares
# Modeler / Antares-Xpansion) is configured with below, so the comparison measures the
# Benders decomposition's speedup, not a difference in LP solver implementation.
PYPSA_SOLVER_NAME = "cbc"
GEMS_SOLVER_NAME = "coin"

logger = logging.getLogger("xpansion_benchmark")
logger.setLevel(logging.INFO)

# (n_buses, n_timesteps, study_name) — sized so each operational subproblem is nontrivial
# and scenario scaling can expose Benders' advantage over a monolithic PyPSA LP.
STUDIES = [
    (20, 168, "synthetic_mesh_20x168"),
    (40, 168, "synthetic_mesh_40x168"),
    (40, 672, "synthetic_mesh_40x672"),
]

# PyPSAStudyConverter only writes the antares-xpansion-launcher (Benders) study when there
# are >= 2 scenarios; with exactly 1 scenario extendable capacity is a plain LP variable
# solved by antares-modeler. Scenario counts therefore start at 2.
SCENARIO_COUNTS = [2, 10, 50, 100]


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
    """Fan a single-scenario network into n equal-weight scenarios with independent load noise."""
    rng = np.random.default_rng(seed)
    scenario_names = [f"scenario_{i}" for i in range(n_scenarios)]
    weight = 1.0 / n_scenarios
    network.set_scenarios(dict.fromkeys(scenario_names, weight))

    factors = rng.uniform(_LOAD_NOISE_LOW, _LOAD_NOISE_HIGH, size=n_scenarios)
    for scenario_name, factor in zip(scenario_names, factors):
        network.loads_t.p_set[scenario_name] = network.loads_t.p_set[scenario_name] * factor
    return network


def build_synthetic_investment_network(n_buses: int, n_timesteps: int, *, name: str) -> Network:
    """Connected AC-ring investment network: extendable gen + load per bus, Lines only."""
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


@pytest.mark.parametrize("n_buses, n_timesteps, study_name", STUDIES)
@pytest.mark.parametrize("n_scenarios", SCENARIO_COUNTS)
def test_xpansion_vs_pypsa_scenario_scaling(
    n_buses: int, n_timesteps: int, study_name: str, n_scenarios: int
) -> None:
    if not (PROJECT_ROOT / get_antares_dir_name()).is_dir():
        pytest.skip(
            f"Antares binaries not found. Please download version {get_antares_version()} from "
            "https://github.com/AntaresSimulatorTeam/Antares_Simulator/releases"
        )
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
    # Converter: PyPSA -> GEMS study (Xpansion path via extendable generators)
    # ==================================================================================
    study_dir = PROJECT_ROOT / "tmp" / run_name
    start_time_conversion = time.time()
    logger.info("Converting PyPSA network to GEMS study")
    PyPSAStudyConverter(
        pypsa_network=network, study_dir=study_dir, series_file_format=".tsv", solver_name=GEMS_SOLVER_NAME
    ).to_gems_study()
    conversion_time = time.time() - start_time_conversion
    benchmark_data_frame.loc[0, "pypsa_to_gems_conversion_time"] = conversion_time

    study_root = study_dir / "systems"

    # ==================================================================================
    # Antares-Xpansion: run antares-xpansion-launcher (antares-problem-generator + benders)
    # ==================================================================================
    launcher_bin = get_antares_xpansion_launcher_bin(PROJECT_ROOT)
    logger.info("Running Antares-Xpansion launcher on %s", study_root)
    xpansion_start = time.time()
    result = run_xpansion_launcher(study_root, launcher_bin, logger=logger)
    xpansion_elapsed = time.time() - xpansion_start
    benchmark_data_frame.loc[0, "xpansion_total_time"] = xpansion_elapsed

    if result.returncode != 0:
        benchmark_data_frame.loc[0, "xpansion_status"] = "FAILED"
        logger.error(
            "antares-xpansion-launcher failed (returncode=%s):\n--- stdout (tail) ---\n%s\n"
            "--- stderr (tail) ---\n%s",
            result.returncode,
            result.stdout[-4000:],
            result.stderr[-2000:],
        )
    else:
        xpansion_solution = read_xpansion_out_json(study_root)["solution"]
        benchmark_data_frame.loc[0, "xpansion_status"] = xpansion_solution["problem_status"]
        benchmark_data_frame.loc[0, "xpansion_objective_value"] = xpansion_solution["overall_cost"]

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
    benchmark_data_frame.loc[0, "pypsa_objective"] = network.objective + network.objective_constant
    benchmark_data_frame.loc[0, "pypsa_solver_name"] = network.model.solver_name

    # ==================================================================================
    # Results: append one row to the combined benchmark CSV
    # ==================================================================================
    results_dir = PROJECT_ROOT / "tmp" / "xpansion_benchmark_results"
    results_dir.mkdir(parents=True, exist_ok=True)
    combined_results_file = results_dir / "xpansion_scenario_results.csv"

    file_exists = combined_results_file.exists()
    benchmark_data_frame.to_csv(combined_results_file, mode="a", header=not file_exists, index=False)
    logger.info("Appended benchmark results to %s", combined_results_file)

    shutil.rmtree(study_dir, ignore_errors=True)
