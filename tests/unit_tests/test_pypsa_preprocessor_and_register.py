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

import logging

import pytest
from pypsa import Network

from src.pypsa_preprocessor import PyPSAPreprocessor
from src.pypsa_register import PyPSARegister

logger = logging.getLogger(__name__)


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


def test_preprocessor_renames_buses_scenarios(scenario_network: Network) -> None:
    logger.info("Running test_preprocessor_renames_buses_scenarios")
    PyPSAPreprocessor(scenario_network).network_preprocessing()
    assert "bus_1" in scenario_network.buses.index.get_level_values(1)
    assert all(" " not in b for b in scenario_network.buses.index.get_level_values(1))


def test_register_outputs_expected_keys_scenarios(scenario_network: Network) -> None:
    logger.info("Running test_register_outputs_expected_keys_scenarios")
    PyPSAPreprocessor(scenario_network).network_preprocessing()
    components, global_constraints = PyPSARegister(scenario_network).register()

    # Component data is keyed by "<PyPSA component type>:<GEMS model>": a type may map onto several models.
    assert {"generators:generator", "loads:load", "buses:bus", "links:link"} <= set(components.keys())

    # No generator has an emitting carrier: PyPSA adds no constraint, neither does the converter.
    assert global_constraints == {}


def test_global_constraint_is_one_component_whatever_the_number_of_scenarios(scenario_network: Network) -> None:
    """One GEMS component per PyPSA GlobalConstraint, with a quota per scenario (not one component per scenario)."""
    scenario_network.carriers.loc[("high", "carrier"), "co2_emissions"] = 0.5
    scenario_network.generators["carrier"] = "carrier"
    scenario_network.global_constraints.loc[("low", "co2"), "constant"] = 50.0
    PyPSAPreprocessor(scenario_network).network_preprocessing()
    _, global_constraints = PyPSARegister(scenario_network).register()

    assert list(global_constraints) == ["global_constraint_co2"]
    co2 = global_constraints["global_constraint_co2"]
    assert co2.gems_model_id == "global_constraint_co2_max"
    assert co2.quota_by_scenario == {"low": 50.0, "medium": 100.0, "high": 100.0}
    assert co2.gems_components_and_ports == [("generator_gen_1", "emission_port")]


def test_bus_theta_bounds_added(base_network: Network) -> None:
    logger.info("Running test_bus_theta_bounds_added")
    PyPSAPreprocessor(base_network).network_preprocessing()
    buses = base_network.buses

    assert "theta_min" in buses.columns
    assert "theta_max" in buses.columns
    # base_network is Links-only: no Line/Transformer couples bus angles, so theta stays free.
    # Fixing unused Slack thetas would emit orphan FX bounds that Clp rejects in Xpansion MPS.
    assert all(buses["theta_min"] == float("-inf"))
    assert all(buses["theta_max"] == float("inf"))


def test_bus_theta_bounds_fixed_only_on_ac_branch_slack(line_network: Network) -> None:
    logger.info("Running test_bus_theta_bounds_fixed_only_on_ac_branch_slack")
    # Island bus connected only by a Link must keep free theta; AC-line Slack is fixed to 0.
    line_network.add("Bus", "island", v_nom=1.0)
    line_network.add(
        "Link",
        "dc_link",
        bus0="bus1",
        bus1="island",
        p_nom=10.0,
        p_min_pu=-1,
        p_max_pu=1,
    )
    PyPSAPreprocessor(line_network).network_preprocessing()
    buses = line_network.buses

    assert buses.loc["bus1", "theta_min"] == 0.0
    assert buses.loc["bus1", "theta_max"] == 0.0
    assert buses.loc["bus2", "theta_min"] == float("-inf")
    assert buses.loc["bus2", "theta_max"] == float("inf")
    assert buses.loc["island", "theta_min"] == float("-inf")
    assert buses.loc["island", "theta_max"] == float("inf")


def test_bus_register_includes_theta_params(base_network: Network) -> None:
    logger.info("Running test_bus_register_includes_theta_params")
    PyPSAPreprocessor(base_network).network_preprocessing()
    components, _ = PyPSARegister(base_network).register()

    bus_data = components["buses:bus"]
    assert "theta_min" in bus_data.pypsa_params_to_gems_params
    assert "theta_max" in bus_data.pypsa_params_to_gems_params
    assert bus_data.pypsa_params_to_gems_params["theta_min"] == "theta_min"
    assert bus_data.pypsa_params_to_gems_params["theta_max"] == "theta_max"


@pytest.fixture()
def line_network() -> Network:
    net = Network(name="Line_Network", snapshots=[0, 1])
    net.add("Bus", "bus1", v_nom=1.0)
    net.add("Bus", "bus2", v_nom=1.0)
    net.add(
        "Line",
        "line1",
        bus0="bus1",
        bus1="bus2",
        x=0.1,
        s_nom=100.0,
        s_nom_extendable=False,
        s_max_pu=1.0,
    )
    return net


def test_line_preprocessing(line_network: Network) -> None:
    logger.info("Running test_line_preprocessing")
    PyPSAPreprocessor(line_network).network_preprocessing()

    assert len(line_network.lines) == 1
    assert "line_line1" in line_network.lines.index
    assert "modular" in line_network.lines.columns
    assert "s_nom_min" in line_network.lines.columns
    assert "s_nom_max" in line_network.lines.columns
    assert line_network.lines.loc["line_line1", "modular"] == 0.0
    assert line_network.lines.loc["line_line1", "s_nom_min"] == 100.0
    assert line_network.lines.loc["line_line1", "s_nom_max"] == 100.0
    assert line_network.lines.loc["line_line1", "capital_cost"] == 0.0
    assert line_network.lines.loc["line_line1", "s_nom_mod"] > 0.0
    # v_nom=1 kV → x_pu = x_ohm / 1² = x_ohm (no conversion)
    assert abs(line_network.lines.loc["line_line1", "x_pu"] - 0.1) < 1e-9


def test_line_register(line_network: Network) -> None:
    logger.info("Running test_line_register")
    PyPSAPreprocessor(line_network).network_preprocessing()
    components, _ = PyPSARegister(line_network).register()

    assert "lines:line" in components
    line_data = components["lines:line"]
    assert line_data.gems_model_id == "line"
    assert "x_pu" in line_data.pypsa_params_to_gems_params
    assert line_data.pypsa_params_to_gems_params["x_pu"] == "x"
    assert "bus0" in line_data.pypsa_params_to_gems_connections
    assert "bus1" in line_data.pypsa_params_to_gems_connections
    assert line_data.pypsa_params_to_gems_connections["bus0"] == ("bus0_p_port", "p_balance_port")
    assert line_data.pypsa_params_to_gems_connections["bus1"] == ("bus1_p_port", "p_balance_port")


@pytest.fixture()
def transformer_network() -> Network:
    net = Network(name="Transformer_Network", snapshots=[0, 1])
    net.add("Bus", "bus_hv", v_nom=110.0)
    net.add("Bus", "bus_lv", v_nom=20.0)
    net.add(
        "Transformer",
        "trafo1",
        bus0="bus_hv",
        bus1="bus_lv",
        x=0.1,
        s_nom=100.0,
        s_nom_extendable=False,
    )
    return net


def test_transformer_preprocessing(transformer_network: Network) -> None:
    logger.info("Running test_transformer_preprocessing")
    PyPSAPreprocessor(transformer_network).network_preprocessing()

    assert len(transformer_network.transformers) == 1
    assert "transformer_trafo1" in transformer_network.transformers.index
    assert "x_pu_eff" in transformer_network.transformers.columns
    assert "modular" in transformer_network.transformers.columns
    assert transformer_network.transformers.loc["transformer_trafo1", "modular"] == 0.0
    assert transformer_network.transformers.loc["transformer_trafo1", "s_nom_min"] == 100.0
    assert transformer_network.transformers.loc["transformer_trafo1", "s_nom_max"] == 100.0
    assert transformer_network.transformers.loc["transformer_trafo1", "capital_cost"] == 0.0


def test_transformer_register(transformer_network: Network) -> None:
    logger.info("Running test_transformer_register")
    PyPSAPreprocessor(transformer_network).network_preprocessing()
    components, _ = PyPSARegister(transformer_network).register()

    assert "transformers:transformer" in components
    trafo_data = components["transformers:transformer"]
    assert trafo_data.gems_model_id == "transformer"
    assert "x_pu_eff" in trafo_data.pypsa_params_to_gems_params
    assert trafo_data.pypsa_params_to_gems_params["x_pu_eff"] == "x_pu_eff"
    assert trafo_data.pypsa_params_to_gems_connections["bus0"] == ("bus0_p_port", "p_balance_port")
    assert trafo_data.pypsa_params_to_gems_connections["bus1"] == ("bus1_p_port", "p_balance_port")
