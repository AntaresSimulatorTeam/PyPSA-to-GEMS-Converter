# Copyright (c) 2025, RTE (https://www.rte-france.com)
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

from pypsa import Network

from src.pypsa_preprocessor import PyPSAPreprocessor
from src.utils import StudyType


def test_rename_buses_2_stage_stochastic_optimization() -> None:
    # Create a simple network with 10 time steps
    network = Network(name="Simple_Network", snapshots=[i for i in range(10)])

    # Add a bus
    # Add the carrier before creating the bus
    network.add("Carrier", "carrier", co2_emissions=0)
    network.add("Bus", "bus1", v_nom=1, carrier="carrier")

    # Add a load
    network.add("Load", "load1", bus="bus1", p_set=100, q_set=0)

    # Add generators with initial p_max_pu values
    network.add(
        "Generator",
        "gen1",
        bus="bus1",  # intentional
        p_nom_extendable=False,
        marginal_cost=50,  # €/MWh
        p_nom=200,  # MW
        p_max_pu=0.9,  # Base value
    )

    network.add(
        "Generator",
        "gen2",
        bus="bus1",  # intentional
        p_nom_extendable=False,
        marginal_cost=10,  # €/MWh
        p_nom=100,  # MW
        p_min_pu=0.0,
        p_max_pu=[0.6 + 0.02 * i for i in range(10)],  # Time-varying base values
    )

    # Define scenarios with probabilities (these will be used as coefficients)
    # Have that on mind this number isn't multiplied with numbers in p_max_pu
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

    PyPSAPreprocessor(network, StudyType.TWO_STAGE_STOCHASTIC).network_preprocessing()  # call preprocessor

    network.optimize()  # check if pypsa can optimize the network,if everything is correctly renamed


def test_rename_buses_2_stage_stochastic_optimization_two() -> None:
    # Create a simple network with 10 time steps
    network = Network(name="Simple_Network", snapshots=[i for i in range(10)])

    # Add a bus
    # Add the carrier before creating the bus
    network.add("Carrier", "carrier", co2_emissions=0)
    network.add("Bus", "bus1", v_nom=1, carrier="carrier")

    # Add a load
    network.add("Load", "load1", bus="bus1", p_set=100, q_set=0)

    # Add generators with initial p_max_pu values
    network.add(
        "Generator",
        "gen1",
        bus="bus1",  # intentional
        p_nom_extendable=False,
        marginal_cost=50,  # €/MWh
        p_nom=200,  # MW
        p_max_pu=0.9,  # Base value
    )

    network.add(
        "Generator",
        "gen2",
        bus="bus1",  # intentional
        p_nom_extendable=False,
        marginal_cost=10,  # €/MWh
        p_nom=100,  # MW
        p_min_pu=0.0,
        p_max_pu=0.7,
    )

    # Define scenarios with probabilities (these will be used as coefficients)
    # Have that on mind this number isn't multiplied with numbers in p_max_pu
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

    PyPSAPreprocessor(network, StudyType.TWO_STAGE_STOCHASTIC).network_preprocessing()  # call preprocessor

    network.optimize()  # check if pypsa can optimize the network,if everything is correctly renamed
