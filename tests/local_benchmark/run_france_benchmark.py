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
Standalone script to run the france_only benchmark: load (snapshot weights set to 1),
preprocess, convert to GEMS at the start, then solve PyPSA on the original network,
then run the modeler on GEMS files. Saves results to tmp/benchmark_results/france_only_results.csv.
Run from project root: python tests/local_benchmark/run_france_benchmark.py
"""

import logging
import shutil
import subprocess
import sys
import time
from pathlib import Path

# Project root on path so "src" and "tests" import (run from project root)
current_dir = Path(__file__).resolve().parents[2]
if str(current_dir) not in sys.path:
    sys.path.insert(0, str(current_dir))

import pandas as pd
import yaml
from highspy import Highs  # type: ignore

from src.dependencies import get_antares_dir_name, get_antares_modeler_bin, get_antares_version
from src.pypsa_converter import PyPSAStudyConverter
from tests.utils import get_objective_value, load_pypsa_study_benchmark, preprocess_network

# Logging to console so you see PyPSA optimize etc.
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    datefmt="%H:%M:%S",
    stream=sys.stdout,
)
logger = logging.getLogger("benchmark")
logger.setLevel(logging.INFO)

FILE_NAME = "france_only.nc"
LOAD_SCALING = 0.8  # scale load by 20% (i.e. 80% of original)
STUDY_NAME = "benchmark_study_france_only"


def main() -> None:
    if not (current_dir / get_antares_dir_name()).is_dir():
        logger.error(
            f"Antares binaries not found. Please download version {get_antares_version()} from "
            "https://github.com/AntaresSimulatorTeam/Antares_Simulator/releases"
        )
        sys.exit(1)

    benchmark_data_frame = pd.DataFrame()
    logger.info("Loading network %s...", FILE_NAME)
    network, parsing_time = load_pypsa_study_benchmark(FILE_NAME, LOAD_SCALING)
    # Converter requires all snapshot_weightings == 1.0
    network.snapshot_weightings.loc[:] = 1.0

    benchmark_data_frame.loc[0, "parsing_time"] = parsing_time
    benchmark_data_frame.loc[0, "pypsa_network_name"] = network.name
    benchmark_data_frame.loc[0, "number_of_time_steps"] = len(network.snapshots)
    benchmark_data_frame.loc[0, "antares_version"] = f"v{get_antares_version()}"

    benchmark_data_frame.loc[0, "number_of_buses"] = len(network.buses)
    benchmark_data_frame.loc[0, "number_of_generators"] = len(network.generators)
    benchmark_data_frame.loc[0, "number_of_loads"] = len(network.loads)
    benchmark_data_frame.loc[0, "number_of_links"] = len(network.links)
    benchmark_data_frame.loc[0, "number_of_storage_units"] = len(network.storage_units)
    benchmark_data_frame.loc[0, "number_of_stores"] = len(network.stores)
    benchmark_data_frame.loc[0, "number_of_lines"] = len(network.lines)
    benchmark_data_frame.loc[0, "number_of_transformers"] = len(network.transformers)
    benchmark_data_frame.loc[0, "number_of_shunt_impedances"] = len(network.shunt_impedances)
    benchmark_data_frame.loc[0, "pypsa_version"] = network.pypsa_version

    logger.info("Preprocessing network...")
    start_time_preprocessing = time.time()
    network = preprocess_network(network, True, True)
    end_time_preprocessing = time.time() - start_time_preprocessing
    benchmark_data_frame.loc[0, "preprocessing_time_pypsa_network"] = end_time_preprocessing

    # --- Convert to GEMS at the start (scenario weights already set to 1 above) ---
    logger.info("Converting PyPSA to GEMS study...")
    start_time_conversion = time.time()
    PyPSAStudyConverter(
        pypsa_network=network,
        logger=logger,
        study_dir=current_dir / "tmp" / STUDY_NAME,
        series_file_format=".tsv",
    ).to_gems_study()
    end_time_conversion = time.time() - start_time_conversion
    benchmark_data_frame.loc[0, "pypsa_to_gems_conversion_time"] = end_time_conversion

    # --- Solve optimization on original PyPSA network ---
    logger.info("Building PyPSA optimization problem (original network)...")
    start_time_build = time.time()
    network.optimize.create_model()
    build_time = time.time() - start_time_build
    benchmark_data_frame.loc[0, "build_optimization_problem_time_pypsa"] = build_time

    logger.info("Solving PyPSA optimization problem (original network)...")
    optimization_time_start = time.time()
    network.optimize.solve_model()
    optimization_time = time.time() - optimization_time_start

    solver = network.model.solver_model
    benchmark_data_frame.loc[0, "number_of_constraints_pypsa"] = solver.getNumRow()
    benchmark_data_frame.loc[0, "number_of_variables_pypsa"] = solver.getNumCol()
    benchmark_data_frame.loc[0, "pypsa_optimization_time"] = optimization_time
    benchmark_data_frame.loc[0, "total_time_pypsa"] = optimization_time + build_time
    benchmark_data_frame.loc[0, "solver_name_pypsa"] = network.model.solver_name
    benchmark_data_frame.loc[0, "solver_version_pypsa"] = network.model.solver_model.version()
    benchmark_data_frame.loc[0, "pypsa_objective"] = network.objective + network.objective_constant
    logger.info("PyPSA objective value: %s", benchmark_data_frame.loc[0, "pypsa_objective"])

    # --- Run modeler on GEMS files ---
    modeler_bin = get_antares_modeler_bin(current_dir)
    logger.info("Running Antares modeler on GEMS files: %s", current_dir / "tmp" / STUDY_NAME / "systems")

    study_dir = current_dir / "tmp" / STUDY_NAME
    start_time_antares_modeler = time.time()
    result = subprocess.run(
        [str(modeler_bin), str(study_dir / "systems")],
        capture_output=True,
        text=True,
        check=False,
        cwd=str(modeler_bin.parent),
    )
    total_time_antares_modeler = time.time() - start_time_antares_modeler
    benchmark_data_frame.loc[0, "modeler_total_time"] = total_time_antares_modeler

    if result.returncode != 0:
        logger.warning("Modeler stderr: %s", result.stderr[:500] if result.stderr else "")

    output_dir = study_dir / "systems" / "output"
    result_files = [f for f in output_dir.iterdir() if f.is_file() and f.name.startswith("simulation_table")]

    if result_files:
        objective_value = get_objective_value(result_files[-1])
        benchmark_data_frame.loc[0, "modeler_objective_value"] = objective_value
        logger.info("Modeler objective value (GEMS): %s", objective_value)

    mps_files = [f for f in output_dir.iterdir() if f.is_file() and f.name.endswith(".mps") and f.name != "master.mps"]
    if mps_files:
        highs = Highs()
        highs.readModel(str(mps_files[0]))
        lp = highs.getLp()
        benchmark_data_frame.loc[0, "number_of_constraints_modeler"] = lp.num_row_
        benchmark_data_frame.loc[0, "number_of_variables_modeler"] = lp.num_col_

    parameters_yml_path = current_dir / "tmp" / STUDY_NAME / "systems" / "parameters.yml"
    with Path(parameters_yml_path).open() as f:
        parameters_yml = yaml.safe_load(f)
        benchmark_data_frame.loc[0, "modeler_solver_parameters"] = parameters_yml["solver-parameters"]
        benchmark_data_frame.loc[0, "modeler_solver_name"] = parameters_yml["solver"]

    shutil.rmtree(current_dir / "tmp" / STUDY_NAME)

    results_dir = current_dir / "tmp" / "benchmark_results"
    results_dir.mkdir(parents=True, exist_ok=True)
    results_file = results_dir / "france_only_results.csv"
    benchmark_data_frame.to_csv(results_file, mode="w", header=True, index=False)
    logger.info("Saved France benchmark results to %s", results_file)


if __name__ == "__main__":
    main()
