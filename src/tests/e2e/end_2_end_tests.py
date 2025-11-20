from ..utils import load_pypsa_study, extend_quota, replace_lines_by_links
from src.pypsa_converter import PyPSAStudyConverter
from pathlib import Path
from pypsa import Network
import logging
import pytest
import subprocess
import math

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)
current_dir = Path(__file__).resolve().parents[3]

def execute_original_pypsa_study(network: Network,quota: bool):
    if quota:
        network = extend_quota(network)
        
    network = replace_lines_by_links(network)
    
    logger.info("Optimizing the PyPSA study")
    network.optimize()
    logger.info("PyPSA study optimized")
    return network.objective + network.objective_constant


def execute_converted_gems_study(network: Network,quota: bool,study_name: str):
    if quota:
        network = extend_quota(network)
        
    network = replace_lines_by_links(network)

    study_dir = current_dir / "tmp" / study_name
    PyPSAStudyConverter(pypsa_network = network, 
                        logger = logger, 
                        study_dir = study_dir, 
                        series_file_format = ".tsv").to_gems_study()

    modeler_bin = current_dir / "antares-cd-Ubuntu-22.04" / "antares-9.3.1-Ubuntu-22.04" / "bin" / "antares-modeler"
    
    subprocess.run(
        [str(modeler_bin), str(study_dir / "systems")],
        capture_output=True,
        text=True,
        check=False,  
        cwd=str(modeler_bin.parent)  
    )

    output_dir = study_dir / "systems" / "output"
    result_file = [f for f in output_dir.iterdir() if f.is_file() and f.name.startswith("simulation_table")]

    if result_file:
        with open(result_file[0], "rb") as f:
            f.seek(-2, 2)  
            while f.tell() > 0:
                byte = f.read(1)
                if byte == b'\n':
                    break
                f.seek(-2, 1)
            objective_value = float(f.readline().decode().split(",")[-2])
            return objective_value
    
    return float('-inf') 

@pytest.mark.parametrize(
    "file, load_scaling, quota, study_name",
    [
        ("base_s_4_elec.nc", 0.4, True, "study_one"),
        ("simple.nc", 1.0, False, "study_two"),
        ("base_s_6_elec_lvopt_.nc", 0.3, True, "study_three"),
    ],
)
def end_2_end_test(file, load_scaling, quota, study_name):
    if not (Path(current_dir / "antares-cd-Ubuntu-22.04").exists() and Path(current_dir / "antares-9.3.1-Ubuntu-22.04").is_dir()):
        raise FileNotFoundError("Antares binaries not found please download them from https://github.com/AntaresSimulatorTeam/Antares_Simulator/releases")
    
    network = load_pypsa_study(file=file, load_scaling=load_scaling)

    assert math.isclose(execute_original_pypsa_study(network, quota), 
                        execute_converted_gems_study(network, quota, study_name),
                        rel_tol=1e-6)