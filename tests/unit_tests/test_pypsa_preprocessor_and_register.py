# Copyright (c) 2026, RTE (https://www.rte-france.com)
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

import pytest
from pypsa import Network

from src.pypsa_preprocessor import PyPSAPreprocessor
from src.pypsa_register import PyPSARegister
from src.utils import StudyType
from tests.utils import replace_lines_by_links


@pytest.fixture()
def base_network() -> Network:
    net = Network(name="Unit_Network", snapshots=[0, 1])

    net.add("Carrier", "carrier", co2_emissions=0)

    net.add("Bus", "bus 1", v_nom=1, carrier="carrier")
    net.add("Bus", "bus 2", v_nom=1, carrier="carrier")

    net.add("Load", "load 1", bus="bus 1", p_set=100, q_set=0, active=1)

    net.add(
        "Generator",
        "gen 1",
        bus="bus 1",
        p_nom_extendable=False,
        p_nom=10,
        p_nom_min=0,
        marginal_cost=5,
        marginal_cost_quadratic=0,
        active=1,
        committable=False,
    )

    net.add(
        "Link",
        "link 1",
        bus0="bus 1",
        bus1="bus 2",
        p_nom_extendable=False,
        p_nom=20,
        p_min_pu=-1,
        p_max_pu=1,
        efficiency=1,
        active=1,
        marginal_cost=0,
        capital_cost=0,
    )

    net.add(
        "GlobalConstraint",
        "co2",
        type="primary_energy",
        sense="<=",
        carrier_attribute="co2_emissions",
        constant=100.0,
    )

    return net


@pytest.fixture()
def scenario_network(base_network: Network) -> Network:
    base_network.set_scenarios({"low": 0.3, "medium": 0.5, "high": 0.2})
    return base_network


def test_preprocessor_raises_on_lines(base_network: Network) -> None:
    base_network.add(
        "Line",
        "line 1",
        bus0="bus 1",
        bus1="bus 2",
        s_nom_extendable=False,
        s_nom=100,
        x=0.1,
        r=0.01,
    )

    with pytest.raises(ValueError, match="does not support Lines"):
        PyPSAPreprocessor(base_network, StudyType.DETERMINISTIC).network_preprocessing()


def test_preprocessor_adds_fictitious_carrier(base_network: Network) -> None:
    PyPSAPreprocessor(base_network, StudyType.DETERMINISTIC).network_preprocessing()
    assert "null" in base_network.carriers.index


def test_preprocessor_renames_buses_deterministic(base_network: Network) -> None:
    PyPSAPreprocessor(base_network, StudyType.DETERMINISTIC).network_preprocessing()
    assert "bus_1" in base_network.buses.index
    assert all(" " not in bus for bus in base_network.buses.index)


def test_preprocessor_renames_buses_scenarios(scenario_network: Network) -> None:
    PyPSAPreprocessor(scenario_network, StudyType.WITH_SCENARIOS).network_preprocessing()
    assert "bus_1" in scenario_network.buses.index.get_level_values(1)
    assert all(" " not in b for b in scenario_network.buses.index.get_level_values(1))


def test_components_without_carrier_get_null(base_network: Network) -> None:
    PyPSAPreprocessor(base_network, StudyType.DETERMINISTIC).network_preprocessing()
    assert base_network.loads.loc["load_load_1", "carrier"] == "null"


def test_register_outputs_expected_keys_scenarios(scenario_network: Network) -> None:
    PyPSAPreprocessor(scenario_network, StudyType.WITH_SCENARIOS).network_preprocessing()
    components, global_constraints = PyPSARegister(scenario_network, StudyType.WITH_SCENARIOS).register()

    assert {"generators", "loads", "buses", "links"} <= set(components.keys())

    # global constraint keys are (scenario, name)
    assert {k[0] for k in global_constraints if k[1] == "co2"} == {"low", "medium", "high"}


def test_replace_lines_by_links_creates_links_and_removes_lines() -> None:
    network = Network(name="Line_Network", snapshots=[0, 1])

    network.add("Carrier", "carrier", co2_emissions=0)
    network.add("Bus", "bus1", v_nom=1, carrier="carrier")
    network.add("Bus", "bus2", v_nom=1, carrier="carrier")

    network.add(
        "Line",
        "line1",
        bus0="bus1",
        bus1="bus2",
        s_nom_extendable=False,
        s_nom=100,
        x=0.1,
        r=0.01,
    )

    network = replace_lines_by_links(network)

    assert len(network.lines) == 0
    assert len(network.links) == 1
    assert "line1-link-bus1-bus2" in network.links.index
