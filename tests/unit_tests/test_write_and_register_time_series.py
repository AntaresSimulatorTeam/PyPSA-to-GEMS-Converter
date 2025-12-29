import logging
from pathlib import Path

from pypsa import Network

from src.pypsa_converter import PyPSAStudyConverter

logger = logging.getLogger(__name__)


def test_write_and_register_time_series_two_stage_stochastic() -> None:
    # Create a simple network with 10 time steps
    network = Network(name="Simple_Network", snapshots=[i for i in range(10)])

    # Add the carrier before creating the bus
    network.add("Carrier", "carrier", co2_emissions=0)
    network.add("Bus", "bus1", v_nom=1, carrier="carrier")

    # Add one load with static p_set and q_set (constant values)
    network.add("Load", "static_load", bus="bus1", p_set=100, q_set=10)

    # Add one load with time-series p_set and q_set (varying across snapshots)
    time_series_p_set = [100 + 10 * i for i in range(10)]  # e.g., [100, 110, ..., 190]
    time_series_q_set = [20 + 5 * i for i in range(10)]  # e.g., [20, 25, ..., 65]
    network.add("Load", "timeseries_load", bus="bus1", p_set=time_series_p_set, q_set=time_series_q_set)

    # Add a generator for completeness
    network.add(
        "Generator",
        "gen1",
        bus="bus1",  # intentional
        p_nom_extendable=False,
        marginal_cost=50,  # €/MWh
        p_nom=200,  # MW
        p_max_pu=[0.9 + 0.01 * i for i in range(10)],  # Base value
    )

    network.add(
        "Generator",
        "gen2",
        bus="bus1",  # intentional
        p_nom_extendable=False,
        marginal_cost=10,  # €/MWh
        p_nom=100,  # MW
        p_min_pu=0.0,
        p_max_pu=[0.7 + 0.01 * i for i in range(10)],
    )

    network.add(
        "Generator",
        "gen3",
        bus="bus1",  # intentional
        p_nom_extendable=False,
        marginal_cost=10,  # €/MWh
        p_nom=100,  # MW
        p_min_pu=0.0,
        p_max_pu=0.9,
    )

    scenarios = {
        "low": 0.3,
        "medium": 0.5,
        "high": 0.2,
    }

    # test do we have scenarios (check if attribute exists)
    if hasattr(network, "has_scenarios"):
        assert not network.has_scenarios

    # Set scenarios in the network
    network.set_scenarios(scenarios)

    # test do we have scenarios
    assert hasattr(network, "has_scenarios")
    if hasattr(network, "has_scenarios"):
        assert network.has_scenarios

    PyPSAStudyConverter(
        network, logger, Path("tmp") / "test_write_and_register_time_series_two_stage_stochastic", "csv"
    ).to_gems_study()
