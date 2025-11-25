from ..utils import load_pypsa_study_benchmark,preprocess_network
from ...pypsa_converter import PyPSAStudyConverter
from pypsa import Network
from pypsa.optimization.optimize import create_model
import pytest
import pandas as pd
import logging
import time
from pathlib import Path

logger = logging.getLogger("benchmark")
logger.setLevel(logging.INFO)
current_dir = Path(__file__).resolve().parents[3]

@pytest.mark.parametrize(
    "file_name, load_scaling",
    [
        ("base_s_20_elec_nl.nc", 1.0),
    ],
)
def test_start_benchmark(file_name: str, load_scaling: float):
    benchmark_data_frame = pd.DataFrame()
    network, parsing_time = load_pypsa_study_benchmark(file_name, load_scaling)
    benchmark_data_frame.loc[0, "parsing_time"] = parsing_time
    benchmark_data_frame.loc[0, "pypsa_network_name"] = network.name
    benchmark_data_frame.loc[0, "number_of_time_steps"] = len(network.snapshots)
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
    benchmark_data_frame.loc[0, "antares_version"] = "9.3.2-rc4"

    network = preprocess_network(network, True, True)
    PyPSAStudyConverter(pypsa_network = network, 
                        logger = logger, 
                        study_dir = current_dir / "tmp" / "benchmark_study", 
                        series_file_format = ".tsv").to_gems_study()

    start_time_build_model = time.time()
    model = create_model(network) # to check is there built in field/function 
    build_time = time.time() - start_time_build_model
    benchmark_data_frame.loc[0, "number_of_constraints_pypsa"] = len(model.constraints)
    benchmark_data_frame.loc[0, "number_of_variables_pypsa"] = len(model.variables)
    benchmark_data_frame.loc[0, "build_optimization_problem_time_pypsa"] = build_time

    
    logger.info("Optimizing the PyPSA study")
    start_time = time.time()
    # optimize with linopy function
    network.optimize()
    total_time = time.time() - start_time
    logger.info("PyPSA study optimized")
    print(f"Solver options: {network.model.solver_model}")
    #benchmark_data_frame.loc[0, "solver_options"] = network.optimize.solver_options
    benchmark_data_frame.loc[0, "solver_name_pypsa"] = network.model.solver_name
    benchmark_data_frame.loc[0, "solver_version_pypsa"] = network.model.solver_model.version()
    benchmark_data_frame.loc[0, "total_optimization_time"] = total_time
    benchmark_data_frame.loc[0, "pypsa_objective"] = network.objective + network.objective_constant

    #execue modeler optimization 
    #read solver name and solver version, could be static 



    return benchmark_data_frame
