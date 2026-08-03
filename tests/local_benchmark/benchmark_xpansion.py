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
PyPSA vs antares-xpansion-launcher speed benchmark on a multi-scenario INVESTMENT study.

Builds a single, normal-sized, extendable network (a 20-bus ring: 168 snapshots, 20 buses, 20
extendable generators, 20 extendable lines -- see ``_build_ring_network``) and re-runs the SAME
network at growing scenario counts (1, 2, 4, 8, 16, 32 Monte-Carlo years), each time via
``network.set_scenarios`` broadcasting the same single-scenario data with equal weights (see
``PyPSAStudyConverter._validate_equal_scenario_weights`` -- equal weights are the only supported
case today). This isolates how solve time scales with scenario count for each engine,
independently of network size:

- A single scenario has no investment decomposition to benefit from and is solved directly by
  antares-modeler (same path as ``tests/local_benchmark/benchmark.py``).
- >=2 scenarios make it a genuine Benders decomposition problem, handed to
  ``antares-xpansion-launcher`` (see ``PyPSAStudyConverter.to_gems_study``'s multi-scenario branch,
  and ``tests/e2e/test_xpansion_study_comparison.py`` for the same automatic wiring).

Why a synthetic network instead of an existing ``resources/test_files/*.nc`` study
------------------------------------------------------------------------------------
Every one of the repo's existing multi-carrier studies (e.g. ``base_s_6_elec_lvopt_.nc``) has at
least one bus connected only through PyPSA ``Link``s (sector-coupling buses such as H2/heat), never
through a genuine ``Line``. ``antares-problem-generator`` v1.9.0 (bundled with antares-xpansion
1.9.0) has a bug where such a bus's unused voltage-angle ("theta") variable gets an MPS bound row
without a declared column, crashing the Benders solve with "invalid status NNNN" as soon as there
is more than one scenario. The purely-Link networks that ARE extendable
(``network_168_*_extendable_gen_nl.nc``) have zero Lines at all and hit the exact same bug. Hence
the ring network below: every bus has a real ``Line`` to its neighbour, so the bug never triggers,
while still being a "normal size", genuinely-meshed investment problem.

How the GEMS side actually runs a multi-scenario Benders problem (the "hybrid study" trick)
------------------------------------------------------------------------------------------------
``antares-xpansion-launcher``'s GEMS driver step does not read `systems/` on its own for the
Benders/MPS-generation pass -- it shells out to ``antares-problem-generator``, which (like
``antares-solver``) only understands classic Antares studies (areas/links/thermal clusters), not a
bare GEMS `system.yml`. ``PyPSAStudyConverter.to_gems_study()`` already builds a real Antares study
to bridge this gap, automatically, via ``src/antares_hybrid_writer.py::AntaresHybridStudyWriter``:
it creates a classic Antares study with antares-craft containing a single "virtual_area" that is
completely inert (no load, no generation, no links -- connected to nothing, so it cannot perturb
the objective), grafts the converted GEMS `system.yml`/model-libraries/data-series directly into
that classic study's own `input/` directory, and sets `nb_years` in `generaldata.ini` to the GEMS
scenario count. antares-problem-generator/antares-solver then load this as an ordinary classic
study, correctly iterating every Monte-Carlo year/scenario, while transparently solving the grafted
GEMS system underneath. This benchmark (like ``tests/e2e/test_xpansion_study_comparison.py``)
exercises that automatic wiring directly -- no extra code needed here to reproduce the trick.

Results are appended to ``tmp/benchmark_results/xpansion_vs_pypsa_results.csv`` for offline analysis
(see ``tests.utils.get_results_path``-style helpers).
"""

import logging
import re
import shutil
import subprocess
import time
from pathlib import Path

import pandas as pd
import pytest
from pypsa import Network

from src.antares_hybrid_writer import AntaresHybridStudyWriter
from src.dependencies import get_antares_dir_name, get_antares_modeler_bin, get_antares_version, get_antares_xpansion_launcher_bin
from src.pypsa_converter import PyPSAStudyConverter
from src.utils import read_xpansion_out_json, run_xpansion_launcher
from tests.utils import PROJECT_ROOT, get_gemspy_version

logger = logging.getLogger("benchmark_xpansion")
logger.setLevel(logging.INFO)

STUDY_NAME = "xpansion_ring_20_buses"
HOURS_PER_WEEK = 168
N_BUSES = 20
SCENARIO_COUNTS = [1, 2, 4, 8, 16, 32]


def _build_ring_network() -> Network:
    """A 20-bus ring, each bus with its own load and extendable generator, buses linked in a
    ring by extendable Lines (see the module docstring for why every bus needs a real Line)."""
    network = Network(name=STUDY_NAME, snapshots=list(range(HOURS_PER_WEEK)))
    network.add("Carrier", "AC", co2_emissions=0)
    for i in range(N_BUSES):
        bus = f"bus{i}"
        network.add("Bus", bus, v_nom=220, carrier="AC")
        network.add(
            "Load", f"load{i}", bus=bus, q_set=0, p_set=[50 + ((i * 7 + h) % 30) for h in range(HOURS_PER_WEEK)]
        )
        network.add(
            "Generator",
            f"gen{i}",
            bus=bus,
            p_nom_extendable=True,
            p_nom_min=100,
            p_nom=100,
            marginal_cost=40 + i,
            capital_cost=800 + 10 * i,
            p_max_pu=[0.9] * HOURS_PER_WEEK,
        )
    for i in range(N_BUSES):
        j = (i + 1) % N_BUSES
        network.add(
            "Line",
            f"line{i}_{j}",
            bus0=f"bus{i}",
            bus1=f"bus{j}",
            x=0.1,
            r=0.01,
            s_nom=100,
            s_nom_extendable=True,
            s_nom_min=100,
            capital_cost=50,
        )
    return network

# Fixed column order for the results CSV: rows come from different branches (antares-modeler vs
# antares-xpansion-launcher, success vs error) with different key sets, so a plain
# pd.DataFrame([row]).to_csv(mode="a") would silently misalign columns across appended rows.
RESULT_COLUMNS = [
    "study_file",
    "n_scenarios",
    "number_of_time_steps",
    "number_of_buses",
    "number_of_generators",
    "number_of_extendable_generators",
    "number_of_links",
    "antares_version",
    "gemspy_version",
    "parsing_time",
    "pypsa_to_gems_conversion_time",
    "gems_engine",
    "gems_wall_time",
    "gems_parsing_time",
    "gems_build_time",
    "gems_solve_time",
    "gems_objective_value",
    "gems_problem_status",
    "gems_error",
    "number_of_variables_gems",
    "number_of_constraints_gems",
    "pypsa_build_time",
    "pypsa_solve_time",
    "pypsa_total_time",
    "pypsa_objective_value",
]


def _pypsa_total_objective(network) -> float:
    return float(network.objective + network.objective_constant)


def _run_antares_modeler(study_dir: Path) -> dict:
    """Run antares-modeler directly (single-scenario baseline) and parse its stdout metrics."""
    antares_modeler_bin = get_antares_modeler_bin(PROJECT_ROOT)
    start = time.time()
    result = subprocess.run(
        [str(antares_modeler_bin), str(study_dir / "systems")],
        capture_output=True,
        text=True,
        check=False,
        cwd=str(antares_modeler_bin.parent),
    )
    wall_time = time.time() - start

    metrics: dict = {"gems_engine": "antares-modeler", "gems_wall_time": wall_time}

    patterns = {
        "gems_parsing_time": (r"Modeler loaded in\s+([0-9.+eE-]+)\s*s", float),
        "gems_build_time": (r"Modeler build took\s+([0-9.+eE-]+)\s*s", float),
        "gems_solve_time": (r"Solved in\s+([0-9.+eE-]+)\s*s", float),
        "number_of_variables_gems": (r"Number of variables:\s*([0-9]+)", int),
        "number_of_constraints_gems": (r"Number of constraints:\s*([0-9]+)", int),
    }
    for line in result.stdout.splitlines():
        for key, (pattern, cast) in patterns.items():
            match = re.search(pattern, line)
            if match:
                try:
                    metrics[key] = cast(match.group(1))
                except ValueError:
                    pass

    output_dir = study_dir / "systems" / "output"
    result_file = next(output_dir.glob("**/simulation_table*"), None)
    if result_file is not None:
        from tests.utils import get_objective_value

        metrics["gems_objective_value"] = get_objective_value(result_file)

    return metrics


def _run_antares_xpansion(gems_dir: Path) -> dict:
    """Run antares-xpansion-launcher (multi-scenario investment path) and parse out.json."""
    antares_hybrid_dir = gems_dir / AntaresHybridStudyWriter.STUDY_NAME
    launcher_bin = get_antares_xpansion_launcher_bin(PROJECT_ROOT)

    start = time.time()
    result = run_xpansion_launcher(antares_hybrid_dir, launcher_bin, logger=logger)
    wall_time = time.time() - start

    metrics: dict = {"gems_engine": "antares-xpansion-launcher", "gems_wall_time": wall_time}
    if result.returncode != 0:
        metrics["gems_error"] = f"returncode={result.returncode}: {result.stderr[-2000:]}"
        return metrics

    out_json = read_xpansion_out_json(antares_hybrid_dir)
    metrics["gems_solve_time"] = out_json.get("run_duration")
    metrics["gems_objective_value"] = out_json.get("solution", {}).get("overall_cost")
    metrics["gems_problem_status"] = out_json.get("solution", {}).get("problem_status")
    return metrics


@pytest.mark.parametrize("n_scenarios", SCENARIO_COUNTS)
def test_xpansion_vs_pypsa_benchmark(n_scenarios: int) -> None:
    if not (PROJECT_ROOT / get_antares_dir_name()).is_dir():
        pytest.skip(
            f"Antares binaries not found. Please download version {get_antares_version()} from "
            "https://github.com/AntaresSimulatorTeam/Antares_Simulator/releases"
        )
    if n_scenarios > 1 and not get_antares_xpansion_launcher_bin(PROJECT_ROOT).exists():
        pytest.skip(
            "antares-xpansion-launcher not found. Please download it from "
            f"https://github.com/AntaresSimulatorTeam/antares-xpansion/releases into {PROJECT_ROOT}"
        )

    study_name = f"benchmark_xpansion_{n_scenarios}"
    gems_dir = PROJECT_ROOT / "tmp" / study_name
    solver_name = "coin"

    logger.info(f"Running xpansion-vs-pypsa benchmark: {STUDY_NAME} with {n_scenarios} scenario(s)")

    # ==================================================================================
    # PyPSA: build the ring network and fan it out into n_scenarios equal-weight scenarios
    # ==================================================================================
    start_time_build_network = time.time()
    network = _build_ring_network()
    parsing_time = time.time() - start_time_build_network

    if n_scenarios > 1:
        network.set_scenarios({f"scenario_{i}": 1.0 / n_scenarios for i in range(n_scenarios)})

    row: dict = {
        "study_file": STUDY_NAME,
        "n_scenarios": n_scenarios,
        "number_of_time_steps": len(network.snapshots),
        "number_of_buses": len(network.buses),
        "number_of_generators": len(network.generators),
        "number_of_extendable_generators": int(network.generators.p_nom_extendable.sum()),
        "number_of_links": len(network.links),
        "antares_version": f"v{get_antares_version()}",
        "gemspy_version": get_gemspy_version(),
        "parsing_time": parsing_time,
    }

    # ==================================================================================
    # Converter: PyPSA -> GEMS study (auto-wires antares-xpansion for n_scenarios > 1)
    # ==================================================================================
    start_time_conversion = time.time()
    PyPSAStudyConverter(
        pypsa_network=network, study_dir=gems_dir, series_file_format=".tsv", solver_name=solver_name
    ).to_gems_study()
    row["pypsa_to_gems_conversion_time"] = time.time() - start_time_conversion

    # ==================================================================================
    # GEMS side: antares-modeler for a single scenario, antares-xpansion-launcher otherwise
    # ==================================================================================
    if n_scenarios == 1:
        row.update(_run_antares_modeler(gems_dir))
    else:
        row.update(_run_antares_xpansion(gems_dir))

    # ==================================================================================
    # PyPSA: build and solve the same investment problem
    # ==================================================================================
    start_time_build = time.time()
    network.optimize.create_model()
    pypsa_build_time = time.time() - start_time_build

    start_time_solve = time.time()
    network.optimize.solve_model(solver_name="cbc")
    pypsa_solve_time = time.time() - start_time_solve

    row["pypsa_build_time"] = pypsa_build_time
    row["pypsa_solve_time"] = pypsa_solve_time
    row["pypsa_total_time"] = pypsa_build_time + pypsa_solve_time
    row["pypsa_objective_value"] = _pypsa_total_objective(network)

    gems_total = row.get("gems_wall_time")
    if gems_total:
        faster = "pypsa" if row["pypsa_total_time"] < gems_total else row.get("gems_engine", "gems")
        speedup = max(row["pypsa_total_time"], gems_total) / min(row["pypsa_total_time"], gems_total)
        logger.info(
            f"n_scenarios={n_scenarios}: pypsa={row['pypsa_total_time']:.4f}s "
            f"{row.get('gems_engine')}={gems_total:.4f}s -> {faster} is {speedup:.2f}x faster"
        )

    # ==================================================================================
    # Results: append one row to the combined benchmark CSV
    # ==================================================================================
    results_dir = PROJECT_ROOT / "tmp" / "benchmark_results"
    results_dir.mkdir(parents=True, exist_ok=True)
    combined_results_file = results_dir / "xpansion_vs_pypsa_results.csv"

    benchmark_data_frame = pd.DataFrame([row]).reindex(columns=RESULT_COLUMNS)
    file_exists = combined_results_file.exists()
    benchmark_data_frame.to_csv(combined_results_file, mode="a", header=not file_exists, index=False)
    logger.info(f"Appended benchmark results to {combined_results_file}")

    shutil.rmtree(gems_dir, ignore_errors=True)
