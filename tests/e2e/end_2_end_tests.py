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
import math
import subprocess
from pathlib import Path

import pytest
from pypsa import Network

from src.dependencies import get_antares_dir_name, get_antares_modeler_bin
from src.pypsa_converter import PyPSAStudyConverter
from tests.utils import get_objective_value, load_pypsa_study, preprocess_network

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)
current_dir = Path(__file__).resolve().parents[2]


# Pytest fixture to check for Antares binaries
@pytest.fixture(scope="function", autouse=True)
def check_antares_binaries() -> None:
    """Check if Antares binaries are available before running tests."""
    antares_dir = current_dir / get_antares_dir_name()
    if not antares_dir.is_dir():
        pytest.skip(
            "Antares binaries not found. Please download them from https://github.com/AntaresSimulatorTeam/Antares_Simulator/releases"
        )


def get_original_pypsa_study_objective(network: Network) -> float:
    logger.info("Optimizing the PyPSA study")
    network.optimize()
    logger.info("PyPSA study optimized")
    return network.objective + network.objective_constant


def get_gems_study_objective(study_name: str) -> float:
    study_dir = current_dir / "tmp" / study_name

    modeler_bin = get_antares_modeler_bin(current_dir)

    logger.info(f"Running Antares modeler with study directory: {study_dir / 'systems'}")

    result = subprocess.run(
        [str(modeler_bin), str(study_dir / "systems")],
        capture_output=True,
        text=True,
        check=False,
        cwd=str(modeler_bin.parent),
    )
    logger.info("================================")
    logger.info("Antares modeler output:")
    logger.info("returncode:", result.returncode)
    logger.info("stdout:", result.stdout)
    logger.info("stderr:", result.stderr)
    logger.info("================================")

    logger.info("Getting Antares study objective")

    output_dir = study_dir / "systems" / "output"
    result_file = [f for f in output_dir.iterdir() if f.is_file() and f.name.startswith("simulation_table")]

    if result_file:
        return get_objective_value(result_file[-1])

    raise FileNotFoundError(f"Result file not found in {output_dir}")


@pytest.mark.parametrize(
    "file, load_scaling, quota, replace_lines, study_name",
    [
        ("base_s_4_elec.nc", 0.4, True, True, "test_one_study_one"),
        ("simple.nc", 1.0, False, True, "test_one_study_two"),
        ("base_s_6_elec_lvopt_.nc", 0.3, True, True, "test_one_study_three"),
    ],
)
def test_end_2_end_test(file: str, load_scaling: float, quota: bool, replace_lines: bool, study_name: str) -> None:
    network = load_pypsa_study(file=file, load_scaling=load_scaling)
    network = preprocess_network(network, quota, replace_lines)
    # Copy before optimize(): get_gems_study_objective needs an un-optimized network (no HiGHS state).
    PyPSAStudyConverter(
        pypsa_network=network, logger=logger, study_dir=current_dir / "tmp" / study_name, series_file_format=".tsv"
    ).to_gems_study()

    assert math.isclose(get_original_pypsa_study_objective(network), get_gems_study_objective(study_name), rel_tol=1e-6)


def test_load_gen() -> None:
    # Function to test the behaviour of Generator with "p_nom_extendable = False"
    network = Network(name="Demo", snapshots=[i for i in range(10)])
    network.add("Bus", "pypsatown", v_nom=1)
    network.add("Load", "pypsaload", bus="pypsatown", p_set=[i * 10 for i in range(10)], q_set=0)
    network.add("Load", "pypsaload2", bus="pypsatown", p_set=100, q_set=0)
    network.add(
        "Generator",
        "pypsagenerator",
        bus="pypsatown",
        p_nom_extendable=False,
        marginal_cost=50,  # €/MWh
        p_nom=200,  # MW
    )
    network.add(
        "Generator",
        "pypsagenerator2",
        bus="pypsatown",
        p_nom_extendable=False,
        marginal_cost=40,  # €/MWh
        p_nom=50,  # MW
    )

    PyPSAStudyConverter(
        pypsa_network=network,
        logger=logger,
        study_dir=current_dir / "tmp" / "test_two_study_one",
        series_file_format=".tsv",
    ).to_gems_study()

    network.optimize()
    assert math.isclose(
        network.objective + network.objective_constant,
        get_gems_study_objective("test_two_study_one"),
        rel_tol=1e-6,
    )


@pytest.mark.parametrize(
    "capital_cost, p_nom_min, p_nom_max, study_name",
    [
        (100.0, 0, 5, "test_three_study_one"),
        (1.0, 0, 5, "test_three_study_two"),
        (1.0, 0, 100, "test_three_study_three"),
        (0.1, 0, 100, "test_three_study_four"),
        (100.0, 10, 50, "test_three_study_five"),
        (100.0, 50, 50, "test_three_study_six"),
    ],
)
def test_load_gen_ext(capital_cost: float, p_nom_min: float, p_nom_max: float, study_name: str) -> None:
    network = Network(name="Demo", snapshots=[i for i in range(10)])
    network.add("Bus", "pypsatown", v_nom=1)
    network.add("Load", "pypsaload", bus="pypsatown", p_set=[i * 10 for i in range(10)], q_set=0)
    network.add("Load", "pypsaload2", bus="pypsatown", p_set=100, q_set=0)
    network.add(
        "Generator",
        "pypsagenerator",
        bus="pypsatown",
        p_nom_extendable=False,
        marginal_cost=50,  # €/MWh
        p_nom=200,  # MW
    )
    network.add(
        "Generator",
        "pypsagenerator2",
        bus="pypsatown",
        p_nom_extendable=True,
        marginal_cost=10,  # €/MWh
        capital_cost=capital_cost,  # €/MWh
        p_nom_min=p_nom_min,  # MW
        p_nom_max=p_nom_max,  # MW
    )

    PyPSAStudyConverter(
        pypsa_network=network, logger=logger, study_dir=current_dir / "tmp" / study_name, series_file_format=".tsv"
    ).to_gems_study()

    network.optimize()
    assert math.isclose(
        network.objective + network.objective_constant, get_gems_study_objective(study_name), rel_tol=1e-6
    )


@pytest.mark.parametrize(
    "ratio, sense, study_name",
    [
        (0, "<=", "test_four_study_one"),
        (0.2, "<=", "test_four_study_two"),
        (0.5, "<=", "test_four_study_three"),
        (1.0, "<=", "test_four_study_four"),
        (0.5, "==", "test_four_study_five"),
        (0.2, "==", "test_four_study_six"),
    ],
)
def test_load_gen_emissions(ratio: float, sense: str, study_name: str) -> None:
    # Testing PyPSA Generators with CO2 constraints
    min_emissions, max_emissions = 10, 20
    network = Network(name="Demo", snapshots=[i for i in range(10)])
    network.add("Carrier", "fictive_fuel_one", co2_emissions=min_emissions)
    network.add("Carrier", "fictive_fuel_two", co2_emissions=max_emissions)
    network.add("Bus", "pypsatown", v_nom=1)
    load1 = [i * 10 for i in range(10)]
    network.add("Load", "pypsaload", bus="pypsatown", p_set=load1, q_set=0)
    load2 = [100 for i in range(10)]
    network.add("Load", "pypsaload2", bus="pypsatown", p_set=load2, q_set=0)
    network.add(
        "Generator",
        "pypsagenerator",
        bus="pypsatown",
        carrier="fictive_fuel_one",
        p_nom_extendable=False,
        marginal_cost=50,  # €/MWh
        p_nom=200,  # MW
    )
    network.add(
        "Generator",
        "pypsagenerator2",
        bus="pypsatown",
        carrier="fictive_fuel_two",
        p_nom_extendable=False,
        marginal_cost=40,  # €/MWh
        p_nom=200,  # MW
    )
    network.add(
        "Generator",
        "pypsagenerator3_emissions_free",
        bus="pypsatown",
        p_nom_extendable=False,
        marginal_cost=50,  # €/MWh
        p_nom=10,  # MW
    )
    quota = (ratio * min_emissions + (1 - ratio) * max_emissions) * (sum(load1) + sum(load2))
    network.add("GlobalConstraint", name="co2_budget", sense=sense, constant=quota)

    PyPSAStudyConverter(
        pypsa_network=network, logger=logger, study_dir=current_dir / "tmp" / study_name, series_file_format=".tsv"
    ).to_gems_study()
    network.optimize()
    assert math.isclose(
        network.objective + network.objective_constant, get_gems_study_objective(study_name), rel_tol=1e-6
    )


def test_load_gen_pmin() -> None:
    # Testing pmin_pu and pmax_pu parameters for Generator component
    # Building the PyPSA test problem
    network = Network(name="Demo", snapshots=[i for i in range(10)])
    network.add("Bus", "pypsatown", v_nom=1)

    network.add("Load", "pypsaload2", bus="pypsatown", p_set=100, q_set=0)
    network.add(
        "Generator",
        "pypsagenerator",
        bus="pypsatown",
        p_nom_extendable=False,
        marginal_cost=50,  # €/MWh
        p_nom=200,  # MW
    )
    network.add(
        "Generator",
        "pypsagenerator2",
        bus="pypsatown",
        pmin_pu=0.1,
        pmax_pu=[0.8 + 0.1 * i for i in range(10)],
        p_nom_extendable=False,
        marginal_cost=10,  # €/MWh
        p_nom=50,  # MW
    )
    PyPSAStudyConverter(
        pypsa_network=network,
        logger=logger,
        study_dir=current_dir / "tmp" / "test_five_study_one",
        series_file_format=".tsv",
    ).to_gems_study()
    network.optimize()
    assert math.isclose(
        network.objective + network.objective_constant,
        get_gems_study_objective("test_five_study_one"),
        rel_tol=1e-6,
    )


def test_load_gen_sum() -> None:
    # Testing e_sum parameters for Generator component

    # Building the PyPSA test problem
    network = Network(name="Demo", snapshots=[i for i in range(10)])
    network.add("Bus", "pypsatown", v_nom=1)

    network.add("Load", "pypsaload2", bus="pypsatown", p_set=100, q_set=0)
    network.add(
        "Generator",
        "pypsagenerator",
        bus="pypsatown",
        p_nom_extendable=False,
        marginal_cost=50,  # €/MWh
        p_nom=200,  # MW
    )
    network.add(
        "Generator",
        "pypsagenerator2",
        bus="pypsatown",
        e_sum_max=200,
        p_nom_extendable=False,
        marginal_cost=10,  # €/MWh
        p_nom=50,  # MW
    )

    PyPSAStudyConverter(
        pypsa_network=network,
        logger=logger,
        study_dir=current_dir / "tmp" / "test_six_study_one",
        series_file_format=".tsv",
    ).to_gems_study()
    network.optimize()
    assert math.isclose(
        network.objective + network.objective_constant,
        get_gems_study_objective("test_six_study_one"),
        rel_tol=1e-6,
    )


def test_load_gen_link() -> None:
    network = Network(name="Demo2", snapshots=[i for i in range(10)])
    network.add("Bus", "pypsatown", v_nom=1)
    network.add("Load", "pypsaload", bus="pypsatown", p_set=[i * 10 for i in range(10)], q_set=0)
    network.add("Load", "pypsaload2", bus="pypsatown", p_set=100, q_set=0)
    network.add(
        "Generator",
        "pypsagenerator",
        bus="pypsatown",
        p_nom_extendable=False,
        marginal_cost=50,  # €/MWh
        p_nom=200,  # MW
    )
    network.add(
        "Generator",
        "pypsagenerator2",
        bus="pypsatown",
        p_nom_extendable=False,
        marginal_cost=40,  # €/MWh
        p_nom=50,  # MW
    )
    network.add("Bus", "paris", v_nom=1)
    network.add("Load", "parisload", bus="paris", p_set=200, q_set=0)
    network.add(
        "Generator",
        "pypsagenerator3",
        bus="paris",
        p_nom_extendable=False,
        marginal_cost=200,  # €/MWh
        p_nom=200,  # MW
    )
    network.add(
        "Link",
        "link-paris-pypsatown",
        bus0="pypsatown",
        bus1="paris",
        efficiency=0.9,
        marginal_cost=0.5,
        p_nom=50,
        p_min_pu=-1,
        p_max_pu=1,
    )

    PyPSAStudyConverter(
        pypsa_network=network,
        logger=logger,
        study_dir=current_dir / "tmp" / "test_seven_study_one",
        series_file_format=".tsv",
    ).to_gems_study()
    network.optimize()
    assert math.isclose(
        network.objective + network.objective_constant,
        get_gems_study_objective("test_seven_study_one"),
        rel_tol=1e-6,
    )


@pytest.mark.parametrize(
    "capital_cost, p_nom_min, p_nom_max, study_name",
    [
        (100.0, 0, 50, "test_eight_study_one"),
        (1.0, 0, 50, "test_eight_study_two"),
        (1.0, 0, 100, "test_eight_study_three"),
        (0.1, 0, 100, "test_eight_study_four"),
        (100.0, 10, 50, "test_eight_study_five"),
        (100.0, 50, 50, "test_eight_study_six"),
    ],
)
def test_load_gen_link_ext(capital_cost: float, p_nom_min: float, p_nom_max: float, study_name: str) -> None:
    network = Network(name="Demo2", snapshots=[i for i in range(10)])
    network.add("Bus", "pypsatown", v_nom=1)
    network.add("Load", "pypsaload", bus="pypsatown", p_set=[i * 10 for i in range(10)], q_set=0)
    network.add("Load", "pypsaload2", bus="pypsatown", p_set=100, q_set=0)
    network.add(
        "Generator",
        "pypsagenerator",
        bus="pypsatown",
        p_nom_extendable=False,
        marginal_cost=50,  # €/MWh
        p_nom=200,  # MW
    )
    network.add(
        "Generator",
        "pypsagenerator2",
        bus="pypsatown",
        p_nom_extendable=False,
        marginal_cost=40,  # €/MWh
        p_nom=50,  # MW
    )
    network.add("Bus", "paris", v_nom=1)
    network.add("Load", "parisload", bus="paris", p_set=200, q_set=0)
    network.add(
        "Generator",
        "pypsagenerator3",
        bus="paris",
        p_nom_extendable=False,
        marginal_cost=200,  # €/MWh
        p_nom=200,  # MW
    )
    network.add(
        "Link",
        "link-paris-pypsatown",
        bus0="pypsatown",
        bus1="paris",
        efficiency=0.9,
        marginal_cost=0.5,
        p_nom_min=p_nom_min,
        p_nom_max=p_nom_max,
        p_nom_extendable=True,
        capital_cost=capital_cost,
        p_min_pu=-1,
        p_max_pu=1,
    )

    PyPSAStudyConverter(
        pypsa_network=network, logger=logger, study_dir=current_dir / "tmp" / study_name, series_file_format=".tsv"
    ).to_gems_study()
    network.optimize()
    assert math.isclose(
        network.objective + network.objective_constant, get_gems_study_objective(study_name), rel_tol=1e-6
    )


@pytest.mark.parametrize(
    "state_of_charge_initial, standing_loss, efficiency_store, inflow_factor, study_name",
    [
        (100.0, 0.01, 0.99, 1e-6, "test_storage_unit_one"),
        (100.0, 0.01, 0.99, 1, "test_storage_unit_two"),
        (0.0, 0.01, 0.98, 1, "test_storage_unit_three"),
        (0.0, 0.05, 0.9, 1, "test_storage_unit_four"),
        (0.0, 0.05, 0.9, 4, "test_storage_unit_five"),
    ],
)
def test_storage_unit(
    state_of_charge_initial: float,
    standing_loss: float,
    efficiency_store: float,
    inflow_factor: float,
    study_name: str,
) -> None:
    network = Network(name="Demo3", snapshots=[i for i in range(20)])
    network.add("Bus", "pypsatown", v_nom=1)
    network.add(
        "Load",
        "pypsaload",
        bus="pypsatown",
        p_set=[
            100,
            160,
            100,
            70,
            90,
            30,
            0,
            150,
            200,
            10,
            0,
            0,
            200,
            240,
            0,
            0,
            20,
            50,
            60,
            50,
        ],
        q_set=0,
    )
    network.add(
        "Generator",
        "pypsagenerator",
        bus="pypsatown",
        p_nom_extendable=False,
        marginal_cost=50,  # €/MWh
        p_nom=150.0,  # MW
    )
    network.add(
        "StorageUnit",
        "pypsastorage",
        bus="pypsatown",
        p_nom=100,  # MW
        max_hours=4,  # Hours of storage at full output
        efficiency_store=efficiency_store,
        efficiency_dispatch=0.85,
        standing_loss=standing_loss,
        state_of_charge_initial=state_of_charge_initial,
        marginal_cost=10.0,  # €/MWh
        marginal_cost_storage=1.5,  # €/MWh
        spill_cost=100.0,  # €/MWh
        p_min_pu=-1,
        p_max_pu=1,
        inflow=[i * inflow_factor for i in range(20)],
        cyclic_state_of_charge=True,
        cyclic_state_of_charge_per_period=True,
    )

    PyPSAStudyConverter(
        pypsa_network=network, logger=logger, study_dir=current_dir / "tmp" / study_name, series_file_format=".tsv"
    ).to_gems_study()
    network.optimize()
    assert math.isclose(
        network.objective + network.objective_constant, get_gems_study_objective(study_name), rel_tol=1e-6
    )


@pytest.mark.parametrize(
    "state_of_charge_initial, standing_loss, efficiency_store, inflow_factor, study_name",
    [
        (100.0, 0.01, 0.99, 1e-6, "test_storage_unit_ext_one"),
        (100.0, 0.01, 0.99, 1, "test_storage_unit_ext_two"),
        (0.0, 0.01, 0.98, 1, "test_storage_unit_ext_three"),
        (0.0, 0.05, 0.9, 1, "test_storage_unit_ext_four"),
        (0.0, 0.05, 0.9, 4, "test_storage_unit_ext_five"),
    ],
)
def test_storage_unit_ext(
    state_of_charge_initial: float,
    standing_loss: float,
    efficiency_store: float,
    inflow_factor: float,
    study_name: str,
) -> None:
    # Function to test the StorageUnit Components with "p_nom_extendable = True"
    # Building the PyPSA test problem with a storage unit
    network = Network(name="Demo3", snapshots=[i for i in range(20)])
    network.add("Bus", "pypsatown", v_nom=1)
    network.add(
        "Load",
        "pypsaload",
        bus="pypsatown",
        p_set=[
            100,
            160,
            100,
            70,
            90,
            30,
            0,
            150,
            200,
            10,
            0,
            0,
            200,
            240,
            0,
            0,
            20,
            50,
            60,
            50,
        ],
        q_set=0,
    )
    network.add(
        "Generator",
        "pypsagenerator",
        bus="pypsatown",
        p_nom_extendable=False,
        marginal_cost=50,  # €/MWh
        p_nom=150.0,  # MW
    )
    network.add(
        "StorageUnit",
        "pypsastorage",
        bus="pypsatown",
        p_nom_min=100,  # MW
        p_nom_max=150,  # MW
        p_nom_extendable=True,
        capital_cost=1,
        max_hours=4,  # Hours of storage at full output
        efficiency_store=efficiency_store,
        efficiency_dispatch=0.85,
        standing_loss=standing_loss,
        state_of_charge_initial=state_of_charge_initial,
        marginal_cost=10.0,  # €/MWh
        marginal_cost_storage=1.5,  # €/MWh
        spill_cost=100.0,  # €/MWh
        p_min_pu=-1,
        p_max_pu=1,
        inflow=inflow_factor,
        cyclic_state_of_charge=True,
        cyclic_state_of_charge_per_period=True,
    )
    PyPSAStudyConverter(
        pypsa_network=network, logger=logger, study_dir=current_dir / "tmp" / study_name, series_file_format=".tsv"
    ).to_gems_study()
    network.optimize()

    assert math.isclose(
        network.objective + network.objective_constant, get_gems_study_objective(study_name), rel_tol=1e-6
    )


@pytest.mark.parametrize(
    "e_initial, standing_loss, study_name",
    [
        (50.0, 0.1, "store_test_case_1"),
        (0.0, 0.01, "store_test_case_2"),
        (0.0, 0.05, "store_test_case_3"),
    ],
)
def test_store(e_initial: float, standing_loss: float, study_name: str) -> None:
    network = Network(name="StoreDemo", snapshots=[i for i in range(20)])
    network.add("Bus", "pypsatown", v_nom=1)
    network.add(
        "Load",
        "pypsaload",
        bus="pypsatown",
        p_set=[
            100,
            160,
            100,
            70,
            90,
            30,
            0,
            150,
            200,
            10,
            0,
            0,
            200,
            240,
            0,
            0,
            20,
            50,
            60,
            50,
        ],
        q_set=0,
    )
    network.add(
        "Generator",
        "pypsagenerator",
        bus="pypsatown",
        p_nom_extendable=False,
        marginal_cost=50,  # €/MWh
        p_nom=150.0,  # MW
    )
    network.add(
        "Store",
        "pypsastore",
        bus="pypsatown",
        e_nom=200,  # MWh
        e_initial=e_initial,
        standing_loss=standing_loss,  # 1% loss per hour
        marginal_cost=10.0,  # €/MWh
        marginal_cost_storage=1.5,  # €/MWh
        e_cyclic=True,
    )
    PyPSAStudyConverter(
        pypsa_network=network, logger=logger, study_dir=current_dir / "tmp" / study_name, series_file_format=".tsv"
    ).to_gems_study()
    network.optimize()
    assert math.isclose(
        network.objective + network.objective_constant, get_gems_study_objective(study_name), rel_tol=1e-6
    )


def test_store_ext() -> None:
    network = Network(name="StoreDemo", snapshots=[i for i in range(20)])
    network.add("Bus", "pypsatown", v_nom=1)
    network.add(
        "Load",
        "pypsaload",
        bus="pypsatown",
        p_set=[
            100,
            160,
            100,
            70,
            90,
            30,
            0,
            150,
            200,
            10,
            0,
            0,
            200,
            240,
            0,
            0,
            20,
            50,
            60,
            50,
        ],
        q_set=0,
    )
    network.add(
        "Generator",
        "pypsagenerator",
        bus="pypsatown",
        p_nom_extendable=False,
        marginal_cost=[i for i in range(20)],  # €/MWh
        p_nom=150.0,  # MW
    )
    network.add(
        "Store",
        "pypsastore",
        bus="pypsatown",
        e_nom_min=10.0,  # MWh
        e_nom_max=1000.0,  # MWh
        e_nom_extendable=True,
        e_initial=100.0,
        capital_cost=10,
        standing_loss=0.1,  # 1% loss per hour
        marginal_cost=1.0,  # €/MWh
        marginal_cost_storage=1.5,  # €/MWh
        e_cyclic=True,
    )

    PyPSAStudyConverter(
        pypsa_network=network,
        logger=logger,
        study_dir=current_dir / "tmp" / "store_test_case_ext",
        series_file_format=".tsv",
    ).to_gems_study()
    network.optimize()
    assert math.isclose(
        network.objective + network.objective_constant,
        get_gems_study_objective("store_test_case_ext"),
        rel_tol=1e-6,
    )
