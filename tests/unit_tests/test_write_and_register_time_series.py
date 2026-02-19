import logging
from pathlib import Path

import pytest
from pypsa import Network

from src.pypsa_converter import PyPSAStudyConverter

logger = logging.getLogger(__name__)


@pytest.fixture()
def base_network() -> Network:
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

    return network


@pytest.fixture()
def scenario_network(base_network: Network) -> Network:
    scenarios = {
        "low": 0.3,
        "medium": 0.5,
        "high": 0.2,
    }

    if hasattr(base_network, "has_scenarios"):
        assert not base_network.has_scenarios

    base_network.set_scenarios(scenarios)

    assert hasattr(base_network, "has_scenarios")
    if hasattr(base_network, "has_scenarios"):
        assert base_network.has_scenarios

    return base_network


def test_write_and_register_time_series_two_stage_stochastic_with_scenario_overrides(scenario_network: Network) -> None:
    logger.info("Running test_write_and_register_time_series_two_stage_stochastic_with_scenario_overrides")
    for key, value in scenario_network.components.generators.static.p_max_pu.items():
        if key == ("low", "gen3"):
            scenario_network.components.generators.static.p_max_pu.loc[key] = value * 0.2  # type: ignore

    print(scenario_network.components.generators.static.p_max_pu)

    PyPSAStudyConverter(
        scenario_network,
        logger,
        Path("tmp") / "test_write_and_register_time_series_two_stage_stochastic_with_scenario_overrides",
        "csv",
    ).to_gems_study()
    logger.info("Conversion done; checking data-series CSV count")

    # Expect one file per scenario-dependent time series:
    # - generators p_max_pu: gen1, gen2  -> 2
    # - loads p_set & q_set: timeseries_load -> 2
    # - generators p_max_pu (static scenarized): gen3 -> +1
    assert (
        len(
            list(
                Path("tmp").glob(
                    "test_write_and_register_time_series_two_stage_stochastic_with_scenario_overrides/systems/input/data-series/*.csv"
                )
            )
        )
        == 5
    )
