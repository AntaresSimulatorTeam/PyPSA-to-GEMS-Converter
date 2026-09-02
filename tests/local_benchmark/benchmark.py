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

import logging
import re
import shutil
import subprocess
import time
from datetime import datetime
from pathlib import Path

import pandas as pd
import pytest
import yaml
from gems_craft.optim_config.parsing import OptimConfig, validate_optim_config
from gems_craft.study.folder import load_study
from gems_runner.simulation.optimization import build_problem
from gems_runner.simulation.simulation_table import SimulationTableBuilder
from gems_runner.simulation.time_block import TimeBlock

from src.dependencies import get_antares_dir_name, get_antares_modeler_bin, get_antares_version
from src.pypsa_converter import PyPSAStudyConverter
from tests.utils import (
    PROJECT_ROOT,
    get_gemspy_version,
    get_objective_value,
    load_pypsa_study_benchmark,
    preprocess_network,
)

logger = logging.getLogger("benchmark")
logger.setLevel(logging.INFO)


@pytest.mark.parametrize(
    "file_name, load_scaling, study_name",
    [
        ("ac-dc-data_nl.nc", 1.0, "benchmark_study_ac_dc_data_nl"),
        ("base_s_20_elec_custom1_nl.nc", 1.0, "benchmark_study_base_s_20_elec_custom1_nl"),
        ("base_s_20_elec_nl.nc", 1.0, "benchmark_study_base_s_20_elec_nl"),
        ("base_s_4_elec.nc", 0.4, "benchmark_study_base_s_4_elec"),
        ("base_s_6_elec_lvopt__nl.nc", 1.0, "benchmark_study_base_s_6_elec_lvopt__nl"),
        ("base_s_6_elec_lvopt_.nc", 0.3, "benchmark_study_base_s_6_elec_lvopt_"),
        ("network_168_10_extendable_gen_nl.nc", 1.0, "benchmark_study_network_168_10_extendable_gen_nl"),
        ("network_168_10_s_nl.nc", 1.0, "benchmark_study_network_168_10_s_nl"),
        ("network_168_30_extendable_gen_nl.nc", 1.0, "benchmark_study_network_168_30_extendable_gen_nl"),
        ("network_168_30_nl.nc", 1.0, "benchmark_study_network_168_30_nl"),
        ("network_168_30_s_nl.nc", 1.0, "benchmark_study_network_168_30_s_nl"),
        ("network_168_60_extendable_gen_nl.nc", 1.0, "benchmark_study_network_168_60_extendable_gen_nl"),
        ("network_168_60_s_nl.nc", 1.0, "benchmark_study_network_168_60_s_nl"),
        ("network_168_nl.nc", 1.0, "benchmark_study_network_168_nl"),
        ("network_1680_30_nl.nc", 1.0, "benchmark_study_network_1680_30_nl"),
        ("network_1680_30_s_nl.nc", 1.0, "benchmark_study_network_1680_30_s_nl"),
        ("network_336_10_s_nl.nc", 1.0, "benchmark_study_network_336_10_s_nl"),
        ("network_336_30_s_nl.nc", 1.0, "benchmark_study_network_336_30_s_nl"),
        ("network_336_60_s_nl.nc", 1.0, "benchmark_study_network_336_60_s_nl"),
        ("network_672_10_s_nl.nc", 1.0, "benchmark_study_network_672_10_s_nl"),
        ("network_8736_30_nl.nc", 1.0, "benchmark_study_network_8736_30_nl"),
        (
            "france_clusters_80_snapshots_168_period_one_week.nc",
            0.4,  # scaled by 40% to make the test case feasible
            "benchmark_study_france_clusters_80_snapshots_168_period_one_week",
        ),
        (
            "france_clusters_50_snapshots_365_period_one_year.nc",
            0.4,  # scaled by 40% to make the test case feasible
            "benchmark_study_france_clusters_50_snapshots_365_period_one_year",
        ),
    ],
)
def test_start_benchmark(file_name: str, load_scaling: float, study_name: str) -> None:
    if not (PROJECT_ROOT / get_antares_dir_name()).is_dir():
        pytest.skip(
            f"Antares binaries not found. Please download version {get_antares_version()} from https://github.com/AntaresSimulatorTeam/Antares_Simulator/releases"
        )
    logger.info(f"Running benchmark for study: {study_name}")
    benchmark_data_frame = pd.DataFrame()

    # ==================================================================================
    # PyPSA: load the input network (.nc) and collect basic network metadata
    # ==================================================================================
    network, parsing_time = load_pypsa_study_benchmark(file_name, load_scaling)
    benchmark_data_frame.loc[0, "parsing_time"] = parsing_time
    benchmark_data_frame.loc[0, "pypsa_network_name"] = network.name
    benchmark_data_frame.loc[0, "number_of_time_steps"] = len(network.snapshots)
    benchmark_data_frame.loc[0, "pypsa_filename"] = file_name
    benchmark_data_frame.loc[0, "antares_version"] = f"v{get_antares_version()}"
    benchmark_data_frame.loc[0, "gemspy_version"] = get_gemspy_version()

    # The available PyPSA components registered in pypsa_converter are:
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
    # Converter requires unity snapshot weightings
    network.snapshot_weightings.loc[:] = 1.0

    # ==================================================================================
    # PyPSA: preprocess the network before conversion
    # ==================================================================================
    logger.info("Preprocessing PyPSA network")
    start_time_preprocessing = time.time()
    network = preprocess_network(network, True)
    end_time_preprocessing = time.time() - start_time_preprocessing
    benchmark_data_frame.loc[0, "preprocessing_time_pypsa_network"] = end_time_preprocessing

    # ==================================================================================
    # Converter: PyPSA -> GEMS study (YAML-based study under tmp/<study_name>/systems)
    # ==================================================================================
    start_time_conversion = time.time()
    logger.info("Converting PyPSA network to GEMS study")
    PyPSAStudyConverter(
        pypsa_network=network, study_dir=PROJECT_ROOT / "tmp" / study_name, series_file_format=".tsv"
    ).to_gems_study()
    end_time_conversion = time.time() - start_time_conversion
    benchmark_data_frame.loc[0, "pypsa_to_gems_conversion_time"] = end_time_conversion

    # ==================================================================================
    # Antares Modeler (binary): run on the converted GEMS study and parse stdout metrics
    # ==================================================================================
    logger.info("Running Antares modeler")
    antares_modeler_bin = get_antares_modeler_bin(PROJECT_ROOT)
    logger.info(f"Running Antares modeler with study directory: {PROJECT_ROOT / 'tmp' / study_name / 'systems'}")

    study_dir = PROJECT_ROOT / "tmp" / study_name
    try:
        result = subprocess.run(
            [str(antares_modeler_bin), str(study_dir / "systems")],
            capture_output=True,
            text=True,
            check=False,
            cwd=str(antares_modeler_bin.parent),
        )
        # Parse Antares modeler stdout for problem size and timing information
        antares_modeler_parsing_time: float | None = None
        antares_modeler_build_time: float | None = None
        antares_modeler_solve_time: float | None = None
        antares_modeler_writing_time: float | None = None
        antares_modeler_n_variables: int | None = None
        antares_modeler_n_constraints: int | None = None

        for line in result.stdout.splitlines():
            # Example lines:
            # "[...][modeler][infos] Modeler loaded in 0.036 s"
            # "[...][modeler][infos] Number of variables: 4872"
            # "[...][modeler][infos] Number of constraints: 5209"
            # "[...][modeler][infos] Modeler build took 0.000 s"
            # "[...][modeler][infos] Solved in 0.003 s"
            # "[...][modeler][infos] Simulation Table is generated in 8 ms"
            if "Modeler loaded in" in line:
                match = re.search(r"Modeler loaded in\s+([0-9.+eE-]+)\s*s", line)
                if match:
                    try:
                        antares_modeler_parsing_time = float(match.group(1))
                    except ValueError:
                        pass
            elif "Number of variables:" in line:
                match = re.search(r"Number of variables:\s*([0-9]+)", line)
                if match:
                    try:
                        antares_modeler_n_variables = int(match.group(1))
                    except ValueError:
                        pass
            elif "Number of constraints:" in line:
                match = re.search(r"Number of constraints:\s*([0-9]+)", line)
                if match:
                    try:
                        antares_modeler_n_constraints = int(match.group(1))
                    except ValueError:
                        pass
            elif "Modeler build took" in line and "s" in line:
                match = re.search(r"Modeler build took\s+([0-9.+eE-]+)\s*s", line)
                if match:
                    try:
                        antares_modeler_build_time = float(match.group(1))
                    except ValueError:
                        pass
            elif "Solved in" in line and "s" in line:
                match = re.search(r"Solved in\s+([0-9.+eE-]+)\s*s", line)
                if match:
                    try:
                        antares_modeler_solve_time = float(match.group(1))
                    except ValueError:
                        pass
            elif "Simulation Table is generated in" in line:
                match = re.search(r"Simulation Table is generated in\s+([0-9.+eE-]+)\s*ms", line)
                if match:
                    try:
                        antares_modeler_writing_time = float(match.group(1)) / 1000.0
                    except ValueError:
                        pass

        if antares_modeler_parsing_time is not None:
            benchmark_data_frame.loc[0, "antares_modeler_parsing_time"] = antares_modeler_parsing_time
        if antares_modeler_build_time is not None:
            benchmark_data_frame.loc[0, "antares_modeler_build_time"] = antares_modeler_build_time
        if antares_modeler_solve_time is not None:
            benchmark_data_frame.loc[0, "antares_modeler_solve_time"] = antares_modeler_solve_time
        if antares_modeler_writing_time is not None:
            benchmark_data_frame.loc[0, "antares_modeler_writing_time"] = antares_modeler_writing_time
        if antares_modeler_n_variables is not None:
            benchmark_data_frame.loc[0, "number_of_variables_antares_modeler"] = antares_modeler_n_variables
        if antares_modeler_n_constraints is not None:
            benchmark_data_frame.loc[0, "number_of_constraints_antares_modeler"] = antares_modeler_n_constraints

        antares_modeler_total = sum(
            t
            for t in [
                antares_modeler_parsing_time,
                antares_modeler_build_time,
                antares_modeler_solve_time,
                antares_modeler_writing_time,
            ]
            if t is not None
        )
        benchmark_data_frame.loc[0, "antares_modeler_total_time"] = antares_modeler_total

    except subprocess.CalledProcessError as e:
        raise RuntimeError(f"Antares modeler failed with error: {e}")

    output_dir = study_dir / "systems" / "output"
    # Antares >= 10.1.1 writes the result into a timestamped run subfolder (output/<timestamp>/simulation_table.csv).
    result_file = next(output_dir.glob("**/simulation_table*"), None)

    if result_file is not None:
        objective_value = get_objective_value(result_file)
        benchmark_data_frame.loc[0, "antares_modeler_objective_value"] = objective_value

    # ==================================================================================
    # Study configuration: read the Antares Modeler-generated parameters.yml
    # This is the single source of truth for:
    # - time scope (first-time-step / last-time-step)
    # - solver (solver / solver-parameters)
    # ==================================================================================
    parameters_yml_path = PROJECT_ROOT / "tmp" / study_name / "systems" / "parameters.yml"
    with Path(parameters_yml_path).open() as f:
        parameters_yml = yaml.safe_load(f)
        benchmark_data_frame.loc[0, "antares_modeler_solver_parameters"] = parameters_yml["solver-parameters"]
        benchmark_data_frame.loc[0, "antares_modeler_solver_name"] = parameters_yml["solver"]

    # ==================================================================================
    # GemsPy (Python): run the converted GEMS study via the gemspy API
    # ==================================================================================
    gemspy_study_dir = study_dir / "systems"
    logger.info("Running GemsPy simulation")

    t0 = time.time()
    gemspy_study = load_study(gemspy_study_dir)
    optim_config = OptimConfig()
    optim_config.time_scope.first_time_step = int(parameters_yml.get("first-time-step", 0))
    optim_config.time_scope.last_time_step = int(parameters_yml.get("last-time-step", 0))

    antares_modeler_solver_name = str(parameters_yml.get("solver", "highs"))
    antares_modeler_solver_parameters = str(parameters_yml.get("solver-parameters", "")).strip()
    optim_config.solver_options.name = antares_modeler_solver_name
    if antares_modeler_solver_name.lower() == "highs" and antares_modeler_solver_parameters:
        optim_config.solver_options.parameters = antares_modeler_solver_parameters.replace("THREADS", "threads")
    else:
        optim_config.solver_options.parameters = antares_modeler_solver_parameters
    validate_optim_config(optim_config, gemspy_study.system)
    gemspy_parsing_time = time.time() - t0

    run_id = datetime.now().strftime("%Y%m%dT%H%M%S")
    output_dir_gemspy = gemspy_study_dir / "output" / f"gemspy_{run_id}"
    output_dir_gemspy.mkdir(parents=True, exist_ok=True)

    first = optim_config.time_scope.first_time_step
    last = optim_config.time_scope.last_time_step
    timesteps = list(range(first, last + 1))
    block = TimeBlock(0, timesteps)
    scenario_ids = optim_config.scenario_scope.scenario_ids

    t_build = time.time()
    problem = build_problem(gemspy_study, block, scenario_ids, optim_config=optim_config)
    gemspy_build_time = time.time() - t_build

    t_solve = time.time()
    problem.solve(
        solver_name=optim_config.solver_options.name,
        **optim_config.solver_options.parsed_parameters(),
    )
    gemspy_solve_time = time.time() - t_solve

    t_write = time.time()
    gemspy_table = SimulationTableBuilder().build(problem, scenario_ids_remap=scenario_ids, table_id=run_id)
    gemspy_table.to_csv(output_dir_gemspy)
    gemspy_writing_time = time.time() - t_write

    benchmark_data_frame.loc[0, "gemspy_parsing_time"] = gemspy_parsing_time
    benchmark_data_frame.loc[0, "gemspy_build_time"] = gemspy_build_time
    benchmark_data_frame.loc[0, "gemspy_solve_time"] = gemspy_solve_time
    benchmark_data_frame.loc[0, "gemspy_writing_time"] = gemspy_writing_time
    benchmark_data_frame.loc[0, "gemspy_total_time"] = (
        gemspy_parsing_time + gemspy_build_time + gemspy_solve_time + gemspy_writing_time
    )
    benchmark_data_frame.loc[0, "number_of_variables_gemspy"] = problem.linopy_model.nvars
    benchmark_data_frame.loc[0, "number_of_constraints_gemspy"] = problem.linopy_model.ncons
    benchmark_data_frame.loc[0, "gemspy_objective_value"] = problem.objective_value
    benchmark_data_frame.loc[0, "gemspy_solver_name"] = optim_config.solver_options.name
    benchmark_data_frame.loc[0, "gemspy_solver_parameters"] = optim_config.solver_options.parameters or str(
        optim_config.solver_options.parsed_parameters()
    )

    # ==================================================================================
    # PyPSA: build and solve the optimization model (collect solver stats + objective)
    # ==================================================================================
    start_time_build_optimization_problem = time.time()
    logger.info("Building PyPSA optimization problem")
    network.optimize.create_model()
    pypsa_build_time = time.time() - start_time_build_optimization_problem

    benchmark_data_frame.loc[0, "pypsa_build_time"] = pypsa_build_time

    # solve pypsa optimization problem
    optimization_time_start = time.time()
    logger.info("Solving PyPSA optimization problem")
    network.optimize.solve_model()
    pypsa_solve_time = time.time() - optimization_time_start

    solver = network.model.solver_model

    benchmark_data_frame.loc[0, "number_of_constraints_pypsa"] = solver.getNumRow()

    benchmark_data_frame.loc[0, "number_of_variables_pypsa"] = solver.getNumCol()

    benchmark_data_frame.loc[0, "pypsa_solve_time"] = pypsa_solve_time
    benchmark_data_frame.loc[0, "total_time_pypsa"] = pypsa_solve_time + pypsa_build_time

    benchmark_data_frame.loc[0, "solver_name_pypsa"] = network.model.solver_name
    benchmark_data_frame.loc[0, "solver_version_pypsa"] = network.model.solver_model.version()

    benchmark_data_frame.loc[0, "pypsa_objective"] = network.objective + network.objective_constant

    # ==================================================================================
    # Results: append one row to the combined benchmark CSV
    # ==================================================================================
    results_dir = PROJECT_ROOT / "tmp" / "benchmark_results"
    results_dir.mkdir(parents=True, exist_ok=True)
    combined_results_file = results_dir / "all_studies_results.csv"

    file_exists = combined_results_file.exists()
    benchmark_data_frame.to_csv(combined_results_file, mode="a", header=not file_exists, index=False)
    logger.info(f"Appended benchmark results to {combined_results_file}")

    # Clean up temporary files
    shutil.rmtree(PROJECT_ROOT / "tmp" / study_name)
