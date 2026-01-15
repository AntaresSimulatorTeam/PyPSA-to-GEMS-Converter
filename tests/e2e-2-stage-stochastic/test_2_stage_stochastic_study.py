import logging
from pathlib import Path
from pypsa import Network
from src.pypsa_converter import PyPSAStudyConverter

logger = logging.getLogger(__name__)

def test_2_stage_stochastic_study() -> Network:
    network = Network(name="Simple_Network", snapshots=[i for i in range(10)])

    network.add("Carrier", "carrier", co2_emissions=0)
    network.add("Bus", "bus 1", v_nom=1, carrier="carrier")

    network.add("Load", "static_load", bus="bus 1", p_set=100, q_set=10)

    time_series_p_set = [100 + 10 * i for i in range(10)]
    time_series_q_set = [20 + 5 * i for i in range(10)]
    network.add("Load", "timeseries_load", bus="bus 1", p_set=time_series_p_set, q_set=time_series_q_set)

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
        marginal_cost=10,
        p_nom=100,
        p_min_pu=0.0,
        p_max_pu=[0.7 + 0.01 * i for i in range(10)],
    )

    network.add(
        "Generator",
        "gen3",
        bus="bus 1",
        p_nom_extendable=False,
        marginal_cost=10,
        p_nom=100,
        p_min_pu=0.0,
        p_max_pu=0.9,
    )

    scenarios = {
        "low": 1,
        #"medium": 1,
        #"high": 1,
    } # this sum needs to be equal to 1, so current solution is that we only have one scenario


    network.set_scenarios(scenarios)

    for key, value in network.components.generators.static.p_max_pu.items():
        if key == ("low", "gen3"):
            network.components.generators.static.p_max_pu.loc[key] = value * 0.2 


    PyPSAStudyConverter(
        network,
        logger,
        Path("tmp") / "test_2_stage_stochastic_study",
        "csv",
    ).to_gems_study()

    network.optimize()
