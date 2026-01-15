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
from src.pypsa_register import PyPSARegister
from src.utils import StudyType
from tests.utils import replace_lines_by_links



def test_link_model_libraries() -> None:
    # Create a simple network with 10 time steps
    network = Network(name="Simple_Network", snapshots=[i for i in range(10)])

    # Add the carrier before creating the buses
    network.add("Carrier", "carrier", co2_emissions=0)
    network.add("Bus", "bus1", v_nom=1, carrier="carrier")
    network.add("Bus", "bus2", v_nom=1, carrier="carrier")

    # Add loads for completeness
    network.add("Load", "load1", bus="bus1", p_set=100, q_set=0)
    network.add("Load", "load2", bus="bus2", p_set=50, q_set=0)

    # Add generators for completeness
    network.add(
        "Generator",
        "gen1",
        bus="bus1",
        p_nom_extendable=False,
        p_nom=200,
        marginal_cost=50,
        p_nom_min=100,
    )
    network.add(
        "Generator",
        "gen2",
        bus="bus2",
        p_nom_extendable=False,
        p_nom=100,
        marginal_cost=10,
    )

    # Add one line with static s_nom (constant value)
    network.add(
        "Line",
        "static_line",
        bus0="bus1",
        bus1="bus2",
        s_nom_extendable=False,
        s_nom=150,  # MVA - Static value
        x=0.1,  # Reactance
        r=0.01,  # Resistance
    )

    # Add one line with time-series s_nom (varying across snapshots)
    time_series_s_nom = [120 + 5 * i for i in range(10)]  # e.g., [120, 125, ..., 165] MVA

    network.add(
        "Line",
        "timeseries_line",
        bus0="bus1",
        bus1="bus2",
        s_nom_extendable=False,
        s_nom=time_series_s_nom,  # MVA - Time-series value
        x=0.15,  # Reactance
        r=0.02,  # Resistance
    )

    # Replace lines with links
    network = replace_lines_by_links(network)

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

    print("================================================")
    print("network.components.generators.static.p_nom_min: ", network.components.generators.static.p_nom_min)
    for key, value in network.components.generators.static.p_nom_min.items():
        if key == ("low", "gen3"):
            network.components.generators.static.p_max_pu.loc[key] = value * 0.2
    print("================================================")


    PyPSAPreprocessor(network, StudyType.WITH_SCENARIOS).network_preprocessing()  # call preprocessor
    PyPSARegister(network, StudyType.WITH_SCENARIOS).register()

    network.optimize()  # check if pypsa can optimize the network,if everything is correctly renamed


