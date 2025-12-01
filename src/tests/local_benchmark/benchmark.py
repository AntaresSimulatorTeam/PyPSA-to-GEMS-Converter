from ..utils import load_pypsa_study_benchmark,preprocess_network
from ...pypsa_converter import PyPSAStudyConverter
from pypsa.optimization.optimize import create_model
import pytest
import pandas as pd
import logging
import time
from pathlib import Path
import yaml
import shutil
import subprocess

logger = logging.getLogger("benchmark")
logger.setLevel(logging.INFO)
current_dir = Path(__file__).resolve().parents[3]


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
    ],
)
def test_start_benchmark(file_name: str, load_scaling: float, study_name: str):
    if not Path(current_dir / "antares-9.3.2-rc4-Ubuntu-22.04").is_dir():
        pytest.skip("Antares binaries not found. Please download version 9.3.2-rc4 from https://github.com/AntaresSimulatorTeam/Antares_Simulator/releases")


    benchmark_data_frame = pd.DataFrame()
    network, parsing_time = load_pypsa_study_benchmark(file_name, load_scaling)
    benchmark_data_frame.loc[0, "parsing_time"] = parsing_time
    benchmark_data_frame.loc[0, "pypsa_network_name"] = network.name
    benchmark_data_frame.loc[0, "number_of_time_steps"] = len(network.snapshots)

    benchmark_data_frame.loc[0, "antares_version"] = "v9.3.2-rc4"

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


    start_time_preprocessing = time.time()
    network = preprocess_network(network, True, True)
    end_time_preprocessing = time.time() - start_time_preprocessing
    benchmark_data_frame.loc[0, "preprocessing_time_pypsa_network"] = end_time_preprocessing

    start_time_conversion = time.time()
    PyPSAStudyConverter(pypsa_network = network, 
                        logger = logger, 
                        study_dir = current_dir / "tmp" / study_name, 
                        series_file_format = ".tsv").to_gems_study()
    end_time_conversion = time.time() - start_time_conversion
    benchmark_data_frame.loc[0, "pypsa_to_gems_conversion_time"] = end_time_conversion

    modeler_bin = current_dir / "antares-9.3.2-rc4-Ubuntu-22.04" / "bin" / "antares-modeler"

    logger.info(f"Running Antares modeler with study directory: {current_dir / 'tmp' / study_name / 'systems'}")


    study_dir = current_dir / "tmp" / study_name
    start_time_antares_modeler = time.time()
    try:
        subprocess.run(
            [str(modeler_bin), str(study_dir / "systems")],
            capture_output=True,
            text=True,
            check=False,  
            cwd=str(modeler_bin.parent)  
        )
        total_time_antares_modeler = time.time() - start_time_antares_modeler
        benchmark_data_frame.loc[0, "modeler_total_time"] = total_time_antares_modeler

    except subprocess.CalledProcessError as e:
        raise RuntimeError(f"Antares modeler failed with error: {e}")

    output_dir = study_dir / "systems" / "output"
    result_file = [f for f in output_dir.iterdir() if f.is_file() and f.name.startswith("simulation_table")]


    objective_value = None
    if result_file:
        if result_file[-1].suffix == ".csv":
            df = pd.read_csv(result_file[-1])
            objective_value = float(df.iloc[-1, -2])
        elif result_file[-1].suffix == ".tsv":
            df = pd.read_csv(result_file[-1], sep="\t")
            objective_value = float(df.iloc[-1, -2])
    benchmark_data_frame.loc[0, "modeler_objective_value"] = objective_value


    mps_files = [f for f in output_dir.iterdir() if f.is_file() and f.name.endswith(".mps") and f.name != "master.mps"]
    if mps_files:
        mps_file = mps_files[0]
        with open(mps_file, 'r') as f:
            lines = f.readlines()
            if len(lines) > 3 and 'Constraints' in lines[3]:
                constraints = int(lines[3].split(':')[-1].strip())
                benchmark_data_frame.loc[0, "number_of_constraints_modeler"] = constraints
            if len(lines) > 4 and 'Variables' in lines[4]:
                variables = int(lines[4].split(':')[-1].strip())
                benchmark_data_frame.loc[0, "number_of_variables_modeler"] = variables


    parameters_yml_path = current_dir / "tmp" / study_name / "systems" / "parameters.yml"
    with Path(parameters_yml_path).open() as f:
        parameters_yml = yaml.safe_load(f)   
        benchmark_data_frame.loc[0, "modeler_solver_parameters"] = parameters_yml["solver-parameters"]
        benchmark_data_frame.loc[0, "modeler_solver_name"] = parameters_yml["solver"]

    #make pypsa optimization problem equations,constraints,variables
    start_time_build_optimization_problem = time.time()
    create_model(network) 
    build_optimization_problem_time_pypsa = time.time() - start_time_build_optimization_problem

    benchmark_data_frame.loc[0, "build_optimization_problem_time_pypsa"] = build_optimization_problem_time_pypsa
    
    #solve pypsa optimization problem
    total_time_start = time.time()
    # network.optimize.solve_model()  
    # Note: Linopy releases memory after solve_model(), so constraint details aren't accessible. 
    # To access constraint counts, use network.optimize() instead and query network.model.solver_model.
    network.optimize()
    solver = network.model.solver_model


    total_time = time.time() - total_time_start


    #number of constraints
    benchmark_data_frame.loc[0, "number_of_constraints_pypsa"] = solver.getNumRow()

    #number of variables
    benchmark_data_frame.loc[0, "number_of_variables_pypsa"] = solver.getNumCol()

    
    #this is approximately the time spent solving the problem,because we use create_model from linopy,pypsa has internal logic inside .optimize()
    #but if we call network.optimize.solve_model() we cannot get the number of constraints and variables
    benchmark_data_frame.loc[0, "pypsa_optimization_time"] = total_time - build_optimization_problem_time_pypsa
    benchmark_data_frame.loc[0, "total_time_pypsa"] = total_time

    
    benchmark_data_frame.loc[0, "solver_name_pypsa"] = network.model.solver_name
    benchmark_data_frame.loc[0, "solver_version_pypsa"] = network.model.solver_model.version()
    benchmark_data_frame.loc[0, "pypsa_objective"] = network.objective + network.objective_constant

    
    shutil.rmtree(current_dir / "tmp" / study_name)


    # Save/append to combined results file
    results_dir = current_dir / "tmp" / "benchmark_results"
    results_dir.mkdir(parents=True, exist_ok=True)
    combined_results_file = results_dir / "all_studies_results.csv"
    
    file_exists = combined_results_file.exists()
    benchmark_data_frame.to_csv(
        combined_results_file, 
        mode='a',
        header=not file_exists,  
        index=False
    )
    logger.info(f"Appended benchmark results to {combined_results_file}")
