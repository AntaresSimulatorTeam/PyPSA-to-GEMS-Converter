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

from __future__ import annotations

import json
import logging
import re
import time
from pathlib import Path

import pandas as pd
import pytest
from pypsa import Network

from src.dependencies import (
    get_antares_dir_name,
    get_antares_problem_generator_bin,
    get_antares_version,
    get_antares_xpansion_benders_bin,
    get_antares_xpansion_dir_name,
    get_antares_xpansion_version,
)
from src.pypsa_converter import PyPSAStudyConverter
from src.utils import prepare_benders_runtime_files
from tests.utils import (
    PROJECT_ROOT,
    load_pypsa_study_benchmark,
    preprocess_network,
    run_logged_subprocess,
)

logger = logging.getLogger("benchmark_xpansion")
logger.setLevel(logging.INFO)


def _pypsa_total_objective(n: Network) -> float:
    assert n.objective is not None
    assert n.objective_constant is not None
    return float(n.objective + n.objective_constant)


def _pypsa_problem_sizes(network: Network) -> tuple[int, int]:
    """Variable/constraint counts after solve (benchmark.py uses solver_model when available)."""
    model = network.model
    solver = model.solver_model
    if solver is not None and hasattr(solver, "getNumCol") and hasattr(solver, "getNumRow"):
        return int(solver.getNumCol()), int(solver.getNumRow())
    return int(model.nvars), int(model.ncons)


def _parse_antares_stdout_sizes(text: str) -> tuple[int | None, int | None]:
    """Parse Antares tool stdout for problem size (same lines as antares-modeler in benchmark.py)."""
    n_variables: int | None = None
    n_constraints: int | None = None
    for line in text.splitlines():
        if "Number of variables:" in line:
            match = re.search(r"Number of variables:\s*([0-9]+)", line)
            if match:
                try:
                    n_variables = int(match.group(1))
                except ValueError:
                    pass
        elif "Number of constraints:" in line:
            match = re.search(r"Number of constraints:\s*([0-9]+)", line)
            if match:
                try:
                    n_constraints = int(match.group(1))
                except ValueError:
                    pass
    return n_variables, n_constraints


def _read_mps_comment_sizes(mps_path: Path) -> tuple[int | None, int | None]:
    """Read '* Number of variables/constraints:' from an Antares-generated MPS header."""
    if not mps_path.is_file():
        return None, None
    return _parse_antares_stdout_sizes(mps_path.read_text(encoding="utf-8", errors="ignore")[:4096])


@pytest.mark.parametrize(
    "file_name, load_scaling, study_name",
    [
        (
            "france_clusters_80_snapshots_168_period_one_week_2_scenarios.nc",
            1.0,
            "xpansion_benchmark_france_clusters_80_snapshots_168_period_one_week_2_scenarios",
        ),
        (
            "france_clusters_50_snapshots_365_period_one_year_2_scenarios.nc",
            1.0,
            "xpansion_benchmark_france_clusters_50_snapshots_365_period_one_year_2_scenarios",
        ),
    ],
)
def test_xpansion_benchmark_two_scenarios(file_name: str, load_scaling: float, study_name: str) -> None:
    """Benchmark PyPSA stochastic vs Antares Xpansion (problem-generator + benders) on 2-scenario studies."""

    if not (PROJECT_ROOT / get_antares_dir_name()).is_dir():
        pytest.skip(
            f"Antares binaries not found. Please download version {get_antares_version()} "
            "from https://github.com/AntaresSimulatorTeam/Antares_Simulator/releases"
        )
    if not (PROJECT_ROOT / get_antares_xpansion_dir_name()).is_dir():
        pytest.skip(
            f"Antares Xpansion binaries not found. Please download version {get_antares_xpansion_version()} "
            "from https://github.com/AntaresSimulatorTeam/antares-xpansion/releases"
        )

    logger.info("Running Xpansion benchmark for study: %s", study_name)
    df = pd.DataFrame()

    # --- Load + preprocess ---
    network, parsing_time = load_pypsa_study_benchmark(file_name, load_scaling)
    df.loc[0, "pypsa_filename"] = file_name
    df.loc[0, "pypsa_version"] = network.pypsa_version
    df.loc[0, "parsing_time"] = parsing_time
    df.loc[0, "number_of_time_steps"] = len(network.snapshots)
    df.loc[0, "antares_version"] = f"v{get_antares_version()}"
    df.loc[0, "antares_xpansion_version"] = f"v{get_antares_xpansion_version()}"

    if not (hasattr(network, "has_scenarios") and network.has_scenarios and len(network.scenarios) == 2):
        raise AssertionError(f"Expected exactly 2 scenarios in {file_name}, got: {getattr(network, 'scenarios', None)}")

    # Converter requires unity snapshot weightings
    network.snapshot_weightings.loc[:] = 1.0

    t0 = time.time()
    network = preprocess_network(network, True)
    df.loc[0, "preprocessing_time_pypsa_network"] = time.time() - t0

    # --- PyPSA stochastic solve (reference) ---
    log_dir = PROJECT_ROOT / "tmp" / "benchmark_logs"
    log_dir.mkdir(parents=True, exist_ok=True)
    cbc_log = log_dir / f"pypsa_cbc_{study_name}.log"

    logger.info("Building PyPSA optimization model")
    t_pypsa_build = time.time()
    network.optimize.create_model(include_objective_constant=True)
    df.loc[0, "pypsa_build_seconds"] = time.time() - t_pypsa_build
    logger.info(
        "PyPSA model built in %.1fs (%d variables, %d constraints)",
        df.loc[0, "pypsa_build_seconds"],
        int(network.model.nvars),
        int(network.model.ncons),
    )

    logger.info(
        "Solving PyPSA model with CBC (logLevel=2); solver log also written to %s (tail -f)",
        cbc_log,
    )
    t_pypsa = time.time()
    status, condition = network.optimize.solve_model(
        solver_name="cbc",
        solver_options={"logLevel": 2},
        log_fn=str(cbc_log),
    )
    df.loc[0, "pypsa_solve_seconds"] = time.time() - t_pypsa
    df.loc[0, "pypsa_status"] = status
    df.loc[0, "pypsa_condition"] = condition
    logger.info(
        "PyPSA solve finished in %.1fs: status=%s condition=%s",
        df.loc[0, "pypsa_solve_seconds"],
        status,
        condition,
    )
    df.loc[0, "pypsa_total_objective"] = _pypsa_total_objective(network)
    n_variables_pypsa, n_constraints_pypsa = _pypsa_problem_sizes(network)
    df.loc[0, "number_of_variables_pypsa"] = n_variables_pypsa
    df.loc[0, "number_of_constraints_pypsa"] = n_constraints_pypsa

    # --- Convert to study (multi-scenario => writer generates Antares scaffold + Xpansion config) ---
    study_dir = PROJECT_ROOT / "tmp" / study_name
    logger.info("Converting PyPSA network to GEMS study at %s", study_dir)
    t_conv = time.time()
    PyPSAStudyConverter(pypsa_network=network, study_dir=study_dir, series_file_format=".tsv", solver_name="coin").to_gems_study()
    df.loc[0, "pypsa_to_gems_conversion_time"] = time.time() - t_conv
    logger.info("Conversion finished in %.1fs", df.loc[0, "pypsa_to_gems_conversion_time"])

    study_root = study_dir / "systems"

    # --- Antares problem generation ---
    pg_bin = get_antares_problem_generator_bin(PROJECT_ROOT)
    logger.info("Running antares-problem-generator on %s", study_root)
    t_pg = time.time()
    result = run_logged_subprocess(
        [str(pg_bin), str(study_root)],
        cwd=pg_bin.parent,
        logger=logger,
    )
    df.loc[0, "xpansion_problem_generator_seconds"] = time.time() - t_pg
    df.loc[0, "xpansion_problem_generator_returncode"] = result.returncode
    if result.returncode != 0:
        raise RuntimeError(
            f"antares-problem-generator failed rc={result.returncode}\n"
            f"stdout(last 8000):\n{result.stdout[-8000:]}\n"
            f"stderr(last 8000):\n{result.stderr[-8000:]}\n"
        )

    output_dir, options_path = prepare_benders_runtime_files(study_root)

    # Problem size: antares-problem-generator stdout when present (like antares-modeler in benchmark.py),
    # otherwise from the first Benders subproblem MPS written under output_dir.
    xpansion_n_variables, xpansion_n_constraints = _parse_antares_stdout_sizes(result.stdout)
    if xpansion_n_variables is None or xpansion_n_constraints is None:
        xpansion_n_variables, xpansion_n_constraints = _parse_antares_stdout_sizes(result.stderr)
    if xpansion_n_variables is None or xpansion_n_constraints is None:
        subproblem_mps_files = sorted(output_dir.glob("problem-*.mps"))
        if subproblem_mps_files:
            xpansion_n_variables, xpansion_n_constraints = _read_mps_comment_sizes(subproblem_mps_files[0])
    if xpansion_n_variables is not None:
        df.loc[0, "number_of_variables_xpansion"] = xpansion_n_variables
    if xpansion_n_constraints is not None:
        df.loc[0, "number_of_constraints_xpansion"] = xpansion_n_constraints

    # --- Benders ---
    benders_bin = get_antares_xpansion_benders_bin(PROJECT_ROOT)
    logger.info("Running benders in %s", output_dir)
    t_b = time.time()
    result = run_logged_subprocess(
        [str(benders_bin), str(options_path.name)],
        cwd=output_dir,
        logger=logger,
    )
    df.loc[0, "xpansion_benders_seconds"] = time.time() - t_b
    df.loc[0, "xpansion_benders_returncode"] = result.returncode
    if result.returncode != 0:
        raise RuntimeError(
            f"benders failed rc={result.returncode}\n"
            f"stdout(last 8000):\n{result.stdout[-8000:]}\n"
            f"stderr(last 8000):\n{result.stderr[-8000:]}\n"
        )

    out_json = json.loads((output_dir / "expansion" / "out.json").read_text(encoding="utf-8"))
    sol = out_json["solution"]
    df.loc[0, "xpansion_problem_status"] = sol.get("problem_status")
    df.loc[0, "xpansion_overall_cost"] = sol.get("overall_cost")
    df.loc[0, "xpansion_run_duration_seconds"] = out_json.get("run_duration")

    # --- Store results ---
    results_dir = PROJECT_ROOT / "tmp" / "benchmark_results"
    results_dir.mkdir(parents=True, exist_ok=True)
    results_file = results_dir / "xpansion_benchmark_results.csv"
    df.to_csv(results_file, mode="a", header=not results_file.exists(), index=False)
    logger.info("Appended Xpansion benchmark results to %s", results_file)

