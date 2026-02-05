import logging
from pathlib import Path

from pypsa import Network

from src.pypsa_converter import PyPSAStudyConverter

logger = logging.getLogger(__name__)


def test_converter_deterministic_study() -> None:
    network = Network(name="Simple_Network", snapshots=[i for i in range(10)])
    network.add("Carrier", "carrier", co2_emissions=0)
    network.add("Bus", "bus 1", v_nom=1, carrier="carrier")
    network.add("Load", "static_load", bus="bus 1", p_set=100, q_set=10)
    network.add(
        "Load",
        "timeseries_load",
        bus="bus 1",
        p_set=[100 + 10 * i for i in range(10)],
        q_set=[20 + 5 * i for i in range(10)],
    )
    network.add(
        "Generator",
        "gen1",
        bus="bus 1",
        p_nom_extendable=False,
        marginal_cost=50,
        p_nom=200,
        p_max_pu=[0.9 + 0.01 * i for i in range(10)],
    )
    network.add(
        "Generator",
        "gen2",
        bus="bus 1",
        p_nom_extendable=False,
        marginal_cost=50,
        p_nom=200,
        p_max_pu=[0.9 + 0.01 * i for i in range(10)],
    )
    network.add("Generator", "gen3", bus="bus 1", p_nom_extendable=False, marginal_cost=50, p_nom=200, p_max_pu=0.9)

    PyPSAStudyConverter(network, logger, Path("tmp") / "test_one", "csv").to_gems_study()

    # test if optimi-config isn't generated
    assert not (Path("tmp") / "test_one" / "systems" / "input" / "optim-config.yml").exists()

    network.set_scenarios({"low": 0.5, "high": 0.5})

    PyPSAStudyConverter(network, logger, Path("tmp") / "test_two", "csv").to_gems_study()

    # test if optimi-config is generated
    assert (Path("tmp") / "test_two" / "systems" / "input" / "optim-config.yml").exists()
