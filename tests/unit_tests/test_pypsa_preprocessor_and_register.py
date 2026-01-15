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


def test_rename_buses_2_stage_stochastic_optimization() -> None:
    # Create a simple network with 10 time steps
    network = Network(name="Simple_Network", snapshots=[i for i in range(10)])

    # Add a bus
    # Add the carrier before creating the bus
    network.add("Carrier", "carrier", co2_emissions=0)
    network.add("Bus", "bus 1", v_nom=1, carrier="carrier")

    # Add a load
    network.add("Load", "load 1", bus="bus 1", p_set=100, q_set=0)

    # Add generators with initial p_max_pu values
    network.add(
        "Generator",
        "gen 1",
        bus="bus 1",  # intentional
        p_nom_extendable=False,
        marginal_cost=50,  # €/MWh
        p_nom=200,  # MW
        p_max_pu=0.9,  # Base value
    )

    network.add(
        "Generator",
        "gen 2",
        bus="bus 1",  # intentional
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

    PyPSAPreprocessor(network, StudyType.WITH_SCENARIOS).network_preprocessing()  # call preprocessor

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

    PyPSAPreprocessor(network, StudyType.WITH_SCENARIOS).network_preprocessing()  # call preprocessor
    # PyPSAStudyConverter(pypsa_network=network, logger=logging.getLogger(__name__), study_dir=Path("test_study"), series_file_format=".tsv").to_gems_study()
    PyPSARegister(network, StudyType.WITH_SCENARIOS).register()

    network.optimize()  # check if pypsa can optimize the network,if everything is correctly renamed


def test_load_model_libraries() -> None:
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

    PyPSAPreprocessor(network, StudyType.WITH_SCENARIOS).network_preprocessing()  # call preprocessor
    PyPSARegister(network, StudyType.WITH_SCENARIOS).register()

    network.optimize()  # check if pypsa can optimize the network,if everything is correctly renamed


def test_load_model_libraries_linear_optimal_power_flow() -> None:
    # Create a simple network with 10 time steps
    network = Network(name="Simple_Network", snapshots=[i for i in range(10)])

    # Add the carrier before creating the bus
    network.add("Carrier", "carrier", co2_emissions=0)
    network.add("Bus", "bus 1", v_nom=1, carrier="carrier")

    # Add one load with static p_set and q_set (constant values)
    network.add("Load", "static_load", bus="bus 1", p_set=100, q_set=10)

    # Add one load with time-series p_set and q_set (varying across snapshots)
    time_series_p_set = [100 + 10 * i for i in range(10)]  # e.g., [100, 110, ..., 190]
    time_series_q_set = [20 + 5 * i for i in range(10)]  # e.g., [20, 25, ..., 65]
    network.add("Load", "timeseries_load", bus="bus 1", p_set=time_series_p_set, q_set=time_series_q_set)

    # Add a generator for completeness
    network.add(
        "Generator",
        "gen1",
        bus="bus 1",  # intentional
        p_nom_extendable=False,
        marginal_cost=50,  # €/MWh
        p_nom=200,  # MW
        p_max_pu=[0.9 + 0.01 * i for i in range(10)],  # Base value
    )

    network.add(
        "Generator",
        "gen2",
        bus="bus 1",  # intentional
        p_nom_extendable=False,
        marginal_cost=10,  # €/MWh
        p_nom=100,  # MW
        p_min_pu=0.0,
        p_max_pu=[0.7 + 0.01 * i for i in range(10)],
    )

    PyPSAPreprocessor(network, StudyType.DETERMINISTIC).network_preprocessing()  # call preprocessor
    PyPSARegister(network, StudyType.DETERMINISTIC).register()

    network.optimize()  # check if pypsa can optimize the network,if everything is correctly renamed


def test_generator_model_libraries() -> None:
    # Create a simple network with 10 time steps
    network = Network(name="Simple_Network", snapshots=[i for i in range(10)])

    # Add the carrier before creating the bus
    network.add("Carrier", "carrier", co2_emissions=0)
    network.add("Bus", "bus1", v_nom=1, carrier="carrier")

    # Add a load for completeness
    network.add("Load", "load1", bus="bus1", p_set=100, q_set=0)

    # Add one generator with static p_min_pu, p_max_pu, marginal_cost, and efficiency (constant values)
    network.add(
        "Generator",
        "static_gen",
        bus="bus1",
        p_nom_extendable=False,
        p_nom=200,  # MW
        p_min_pu=0.1,  # Static value
        p_max_pu=0.9,  # Static value
        marginal_cost=50,  # €/MWh - Static value
        efficiency=0.95,  # Static value
        e_sum_min=50,  # Static value for min energy
        e_sum_max=1500,  # Static value for max energy
    )

    # Add one generator with time-series p_min_pu, p_max_pu, marginal_cost, and efficiency (varying across snapshots)
    time_series_p_min_pu = [0.1 + 0.01 * i for i in range(10)]
    time_series_p_max_pu = [0.8 + 0.01 * i for i in range(10)]
    time_series_marginal_cost = [40 + 2 * i for i in range(10)]
    time_series_efficiency = [0.90 + 0.005 * i for i in range(10)]
    network.add(
        "Generator",
        "timeseries_gen",
        bus="bus1",
        p_nom_extendable=False,
        p_nom=150,  # MW
        p_min_pu=time_series_p_min_pu,
        p_max_pu=time_series_p_max_pu,
        marginal_cost=time_series_marginal_cost,
        efficiency=time_series_efficiency,
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

    PyPSAPreprocessor(network, StudyType.WITH_SCENARIOS).network_preprocessing()
    PyPSARegister(network, StudyType.WITH_SCENARIOS).register()

    # check e_sum_min and e_sum_max are set correctly
    generators = network.components.generators
    assert "e_sum_min" not in generators.dynamic
    assert "e_sum_max" not in generators.dynamic
    network.optimize()  # check if pypsa can optimize the network,if everything is correctly renamed


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

    PyPSAPreprocessor(network, StudyType.WITH_SCENARIOS).network_preprocessing()  # call preprocessor
    PyPSARegister(network, StudyType.WITH_SCENARIOS).register()

    network.optimize()  # check if pypsa can optimize the network,if everything is correctly renamed


def test_storage_unit_model_libraries() -> None:
    # Create a simple network with 10 time steps
    network = Network(name="Simple_Network", snapshots=[i for i in range(10)])

    # Add the carrier before creating the bus
    network.add("Carrier", "carrier", co2_emissions=0)
    network.add("Bus", "bus1", v_nom=1, carrier="carrier")

    # Add a load for completeness
    network.add("Load", "load1", bus="bus1", p_set=100, q_set=0)

    # Add generators for completeness
    network.add(
        "Generator",
        "gen1",
        bus="bus1",
        p_nom_extendable=False,
        p_nom=200,
        marginal_cost=50,
    )

    # Add one storage unit with static parameters (constant values)
    network.add(
        "StorageUnit",
        "static_storage",
        bus="bus1",
        p_nom_extendable=False,
        p_nom=100,  # MW
        max_hours=4,  # Hours of storage at full output
        p_min_pu=-1.0,  # Static value (negative for charging)
        p_max_pu=1.0,  # Static value
        spill_cost=100.0,  # €/MWh - Static value
        marginal_cost=10.0,  # €/MWh - Static value
        marginal_cost_storage=1.5,  # €/MWh - Static value
        efficiency_store=0.95,  # Static value
        efficiency_dispatch=0.90,  # Static value
        standing_loss=0.01,  # Static value (1% per hour)
        inflow=0.0,  # Static value (no inflow)
        cyclic_state_of_charge=True,
    )

    # Add one storage unit with time-series parameters (varying across snapshots)
    time_series_p_min_pu = [-1.0 + 0.05 * i for i in range(10)]
    time_series_p_max_pu = [0.8 + 0.02 * i for i in range(10)]
    time_series_spill_cost = [80.0 + 5.0 * i for i in range(10)]
    time_series_marginal_cost = [8.0 + 0.5 * i for i in range(10)]
    time_series_marginal_cost_storage = [1.0 + 0.1 * i for i in range(10)]
    time_series_efficiency_store = [0.90 + 0.005 * i for i in range(10)]
    time_series_efficiency_dispatch = [0.85 + 0.005 * i for i in range(10)]
    time_series_standing_loss = [0.005 + 0.001 * i for i in range(10)]
    time_series_inflow = [0.0 + 2.0 * i for i in range(10)]

    network.add(
        "StorageUnit",
        "timeseries_storage",
        bus="bus1",
        p_nom_extendable=False,
        p_nom=80,  # MW
        max_hours=3,  # Hours of storage at full output
        p_min_pu=time_series_p_min_pu,
        p_max_pu=time_series_p_max_pu,
        spill_cost=time_series_spill_cost,
        marginal_cost=time_series_marginal_cost,
        marginal_cost_storage=time_series_marginal_cost_storage,
        efficiency_store=time_series_efficiency_store,
        efficiency_dispatch=time_series_efficiency_dispatch,
        standing_loss=time_series_standing_loss,
        inflow=time_series_inflow,
        cyclic_state_of_charge=True,
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

    PyPSAPreprocessor(network, StudyType.WITH_SCENARIOS).network_preprocessing()  # call preprocessor
    PyPSARegister(network, StudyType.WITH_SCENARIOS).register()

    network.optimize()  # check if pypsa can optimize the network,if everything is correctly renamed


def test_store_model_libraries() -> None:
    # Create a simple network with 10 time steps
    network = Network(name="Simple_Network", snapshots=[i for i in range(10)])

    # Add the carrier before creating the bus
    network.add("Carrier", "carrier", co2_emissions=0)
    network.add("Bus", "bus1", v_nom=1, carrier="carrier")

    # Add a load for completeness
    network.add("Load", "load1", bus="bus1", p_set=100, q_set=0)

    # Add generators for completeness
    network.add(
        "Generator",
        "gen1",
        bus="bus1",
        p_nom_extendable=False,
        p_nom=200,
        marginal_cost=50,
    )

    # Add one store with static parameters (constant values)
    network.add(
        "Store",
        "static_store",
        bus="bus1",
        e_nom_extendable=False,
        e_nom=150,  # MWh
        e_min_pu=0.1,  # Static value
        e_max_pu=0.9,  # Static value
        marginal_cost=10.0,  # €/MWh - Static value
        marginal_cost_storage=1.5,  # €/MWh - Static value
        standing_loss=0.01,  # Static value (1% per hour)
        e_cyclic=True,
    )

    # Add one store with time-series parameters (varying across snapshots)
    time_series_e_min_pu = [0.1 + 0.01 * i for i in range(10)]
    time_series_e_max_pu = [0.8 + 0.01 * i for i in range(10)]
    time_series_marginal_cost = [8.0 + 0.5 * i for i in range(10)]
    time_series_marginal_cost_storage = [1.0 + 0.1 * i for i in range(10)]
    time_series_standing_loss = [0.005 + 0.001 * i for i in range(10)]

    network.add(
        "Store",
        "timeseries_store",
        bus="bus1",
        e_nom_extendable=False,
        e_nom=120,  # MWh
        e_min_pu=time_series_e_min_pu,
        e_max_pu=time_series_e_max_pu,
        marginal_cost=time_series_marginal_cost,
        marginal_cost_storage=time_series_marginal_cost_storage,
        standing_loss=time_series_standing_loss,
        e_cyclic=True,
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

    PyPSAPreprocessor(network, StudyType.WITH_SCENARIOS).network_preprocessing()  # call preprocessor
    PyPSARegister(network, StudyType.WITH_SCENARIOS).register()

    network.optimize()  # check if pypsa can optimize the network,if everything is correctly renamed
