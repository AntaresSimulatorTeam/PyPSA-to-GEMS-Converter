from pathlib import Path
from pypsa import Network


def load_pypsa_study(file: str, load_scaling: float) -> Network:
    """
    Load a PyPSA study from a NetCDF file, preparing it for analysis or manipulation.
    """
    current_dir = Path(__file__).resolve().parents[2]

    input_file = current_dir / "resources" / "test_files" / file

    network = Network(input_file)

    # Scale the load to make the test case feasible
    network = scale_load(network, load_scaling)

    return network

def load_pypsa_study_benchmark(file: str, load_scaling: float) -> tuple[Network, float]:
    """
    Load a PyPSA study from a NetCDF file, preparing it for analysis or manipulation.
    """
    current_dir = Path(__file__).resolve().parents[2]

    input_file = current_dir / "resources" / "test_files" / file

    start_time = time.time()
    network = Network(input_file)
    end_time = time.time() - start_time
    # Scale the load to make the test case feasible
    network = scale_load(network, load_scaling)

    return tuple([network, end_time])

def scale_load(network: Network, factor: float) -> Network:
    network.loads_t["p_set"] *= factor
    return network

def extend_quota(network: Network) -> Network:
    # Temporary function, used while the GlobalConstraint model is not implemented yet.
    # Set the CO2 bound to very large value
    network.global_constraints["constant"][0] = 10000000000
    return network

def replace_lines_by_links(network: Network) -> Network:
    """
    Replace lines in a PyPSA network with equivalent links.

    This function converts transmission lines to links, which allows for more
    flexible modeling of power flow constraints. Each line is replaced with
    two links (one for each direction) to maintain bidirectional flow capability.
    """

    # Create a copy of the lines DataFrame to iterate over
    lines = network.lines.copy()

    # For each line, create two links (one for each direction)
    for idx, line in lines.iterrows():
        # Get line parameters
        bus0 = line["bus0"]
        bus1 = line["bus1"]
        s_nom = line["s_nom"]
        efficiency = 1.0

        # Add forward link
        network.add(
            "Link",
            f"{idx}-link-{bus0}-{bus1}",
            bus0=bus0,
            bus1=bus1,
            p_min_pu=-1,
            p_max_pu=1,
            p_nom=s_nom,  # Use line capacity as link capacity
            efficiency=efficiency,
        )
    network.remove("Line", lines.index)
    return network

def preprocess_network(network: Network,quota: bool,lines_to_links: bool) -> Network:
    if quota:
        network = extend_quota(network)
    if lines_to_links:
        network = replace_lines_by_links(network)
    return network