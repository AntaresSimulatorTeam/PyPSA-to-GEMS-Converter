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
    logger.info("Optimizing the PyPSA study (network=%s)", network.name)
    network.optimize()
    obj = network.objective + network.objective_constant
    logger.info("PyPSA study optimized; objective=%s", obj)
    return obj


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
    logger.info("Antares modeler output: returncode=%s", result.returncode)
    logger.info("stdout: %s", result.stdout)
    logger.info("stderr: %s", result.stderr)
    logger.info("================================")

    logger.info("Getting Antares study objective")

    output_dir = study_dir / "systems" / "output"
    # Antares >= 10.1.1 writes the result into a timestamped run subfolder (output/<timestamp>/simulation_table.csv).
    result_file = next(output_dir.glob("**/simulation_table*"), None)

    if result_file is not None:
        obj = get_objective_value(result_file)
        logger.info("GEMS study objective for %s: %s", study_name, obj)
        return obj

    raise FileNotFoundError(f"Result file not found in {output_dir}")


@pytest.mark.parametrize(
    "file, load_scaling, quota, study_name",
    [
        ("base_s_4_elec.nc", 0.4, True, "test_one_study_one"),
        ("simple.nc", 1.0, False, "test_one_study_two"),
        ("base_s_6_elec_lvopt_.nc", 0.3, True, "test_one_study_three"),
    ],
)
def test_end_2_end_test(file: str, load_scaling: float, quota: bool, study_name: str) -> None:
    logger.info("Starting e2e test: file=%s, study_name=%s, quota=%s", file, study_name, quota)
    network = load_pypsa_study(file=file, load_scaling=load_scaling)
    logger.info("Loaded PyPSA network from %s", file)
    network = preprocess_network(network, quota)
    logger.info("Preprocessed network; converting to GEMS study %s", study_name)
    # Copy before optimize(): get_gems_study_objective needs an un-optimized network (no HiGHS state).
    PyPSAStudyConverter(
        pypsa_network=network, study_dir=current_dir / "tmp" / study_name, series_file_format=".tsv"
    ).to_gems_study()
    logger.info("Comparing PyPSA vs GEMS objective for %s", study_name)
    assert math.isclose(get_original_pypsa_study_objective(network), get_gems_study_objective(study_name), rel_tol=1e-6)
    logger.info("E2E test passed: %s", study_name)


def test_load_gen() -> None:
    logger.info("Starting test_load_gen: Generator with p_nom_extendable=False")
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
        pypsa_network=network, study_dir=current_dir / "tmp" / study_name, series_file_format=".tsv"
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
    logger.info("Starting test_load_gen_emissions: study_name=%s, ratio=%s, sense=%s", study_name, ratio, sense)
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
        pypsa_network=network, study_dir=current_dir / "tmp" / study_name, series_file_format=".tsv"
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
        pypsa_network=network, study_dir=current_dir / "tmp" / study_name, series_file_format=".tsv"
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
    logger.info(
        "Starting test_storage_unit: study_name=%s, state_of_charge_initial=%s",
        study_name,
        state_of_charge_initial,
    )
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
        pypsa_network=network, study_dir=current_dir / "tmp" / study_name, series_file_format=".tsv"
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
        pypsa_network=network, study_dir=current_dir / "tmp" / study_name, series_file_format=".tsv"
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
        pypsa_network=network, study_dir=current_dir / "tmp" / study_name, series_file_format=".tsv"
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
        study_dir=current_dir / "tmp" / "store_test_case_ext",
        series_file_format=".tsv",
    ).to_gems_study()
    network.optimize()
    assert math.isclose(
        network.objective + network.objective_constant,
        get_gems_study_objective("store_test_case_ext"),
        rel_tol=1e-6,
    )


def test_lines_triangle() -> None:
    """Triangle network (A-B-C) with three fixed-capacity lines."""
    network = Network(name="LinesTriangle", snapshots=list(range(10)))
    network.add("Bus", "A", v_nom=1.0)
    network.add("Bus", "B", v_nom=1.0)
    network.add("Bus", "C", v_nom=1.0)
    network.add("Generator", "G_A", bus="A", p_nom=200, marginal_cost=10)
    network.add("Load", "L_C", bus="C", p_set=[50 + i * 5 for i in range(10)], q_set=0)
    network.add("Line", "AB", bus0="A", bus1="B", x=0.1, s_nom=100)
    network.add("Line", "BC", bus0="B", bus1="C", x=0.1, s_nom=100)
    network.add("Line", "AC", bus0="A", bus1="C", x=0.1, s_nom=100)

    PyPSAStudyConverter(network, current_dir / "tmp" / "line_triangle", ".tsv").to_gems_study()
    network.optimize()
    assert math.isclose(
        network.objective + network.objective_constant, get_gems_study_objective("line_triangle"), rel_tol=1e-6
    )


def test_line_lp() -> None:
    """Triangle network with one extendable LP line."""
    network = Network(name="LineLp", snapshots=list(range(10)))
    network.add("Bus", "A", v_nom=1.0)
    network.add("Bus", "B", v_nom=1.0)
    network.add("Bus", "C", v_nom=1.0)
    network.add("Generator", "G_A", bus="A", p_nom=200, marginal_cost=10)
    network.add("Load", "L_C", bus="C", p_set=[50 + i * 5 for i in range(10)], q_set=0)
    network.add("Line", "AB", bus0="A", bus1="B", x=0.1, s_nom=100, capital_cost=0)
    network.add("Line", "BC", bus0="B", bus1="C", x=0.1, s_nom=100, capital_cost=0)
    network.add(
        "Line", "AC", bus0="A", bus1="C", x=0.1, s_nom_extendable=True, s_nom_min=0, s_nom_max=200, capital_cost=500
    )

    PyPSAStudyConverter(network, current_dir / "tmp" / "line_lp", ".tsv").to_gems_study()
    network.optimize()
    assert math.isclose(
        network.objective + network.objective_constant, get_gems_study_objective("line_lp"), rel_tol=1e-6
    )


def test_line_milp() -> None:
    """Triangle network with three extendable MILP lines."""
    network = Network(name="LineMilp", snapshots=list(range(10)))
    network.add("Bus", "A", v_nom=1.0)
    network.add("Bus", "B", v_nom=1.0)
    network.add("Bus", "C", v_nom=1.0)
    network.add("Generator", "G_A", bus="A", p_nom=200, marginal_cost=10)
    network.add("Load", "L_C", bus="C", p_set=[50 + i * 5 for i in range(10)], q_set=0)
    for line_id, b0, b1 in [("AB", "A", "B"), ("BC", "B", "C"), ("AC", "A", "C")]:
        network.add(
            "Line",
            line_id,
            bus0=b0,
            bus1=b1,
            x=0.1,
            s_nom_extendable=True,
            s_nom_mod=50.0,
            s_nom_min=0.0,
            s_nom_max=200.0,
            capital_cost=800,
        )

    PyPSAStudyConverter(network, current_dir / "tmp" / "line_milp", ".tsv").to_gems_study()
    network.optimize()
    assert math.isclose(
        network.objective + network.objective_constant, get_gems_study_objective("line_milp"), rel_tol=1e-6
    )


def test_transformer_fixed() -> None:
    """HV-LV network with a fixed transformer and fixed line."""
    network = Network(name="TransformerFixed", snapshots=list(range(10)))
    network.add("Bus", "A_HV", v_nom=380.0)
    network.add("Bus", "B_LV", v_nom=110.0)
    network.add("Bus", "C_LV", v_nom=110.0)
    network.add("Generator", "G_A", bus="A_HV", p_nom=200, marginal_cost=10)
    network.add("Generator", "G_C", bus="C_LV", p_nom=50, marginal_cost=80)
    network.add("Load", "L_B", bus="B_LV", p_set=[25 + i for i in range(10)], q_set=0)
    network.add("Load", "L_C", bus="C_LV", p_set=[25 + i for i in range(10)], q_set=0)
    network.add("Transformer", "T_AB", bus0="A_HV", bus1="B_LV", x=0.20, s_nom=150.0, tap_ratio=1.0)
    network.add("Line", "BC", bus0="B_LV", bus1="C_LV", x=0.1, s_nom=100)

    PyPSAStudyConverter(network, current_dir / "tmp" / "transformer_fixed", ".tsv").to_gems_study()
    network.optimize()
    assert math.isclose(
        network.objective + network.objective_constant, get_gems_study_objective("transformer_fixed"), rel_tol=1e-6
    )


def test_transformer_lp() -> None:
    """HV-LV network with an extendable LP transformer."""
    network = Network(name="TransformerLp", snapshots=list(range(10)))
    network.add("Bus", "A_HV", v_nom=380.0)
    network.add("Bus", "B_LV", v_nom=110.0)
    network.add("Bus", "C_LV", v_nom=110.0)
    network.add("Generator", "G_A", bus="A_HV", p_nom=200, marginal_cost=10)
    network.add("Generator", "G_C", bus="C_LV", p_nom=50, marginal_cost=80)
    network.add("Load", "L_B", bus="B_LV", p_set=[25 + i for i in range(10)], q_set=0)
    network.add("Load", "L_C", bus="C_LV", p_set=[25 + i for i in range(10)], q_set=0)
    network.add(
        "Transformer",
        "T_AB",
        bus0="A_HV",
        bus1="B_LV",
        x=0.20,
        s_nom=150.0,
        s_nom_extendable=True,
        s_nom_min=0.0,
        s_nom_max=300.0,
        capital_cost=500,
        tap_ratio=1.0,
    )
    network.add("Line", "BC", bus0="B_LV", bus1="C_LV", x=0.1, s_nom=100)

    PyPSAStudyConverter(network, current_dir / "tmp" / "transformer_lp", ".tsv").to_gems_study()
    network.optimize()
    assert math.isclose(
        network.objective + network.objective_constant, get_gems_study_objective("transformer_lp"), rel_tol=1e-6
    )


def test_scigrid_de() -> None:
    """German transmission grid from pypsa.examples.scigrid_de — 585 buses, 852 lines, 96 transformers, 24 snapshots."""
    network = Network(current_dir / "resources" / "test_files" / "scigrid-de.nc")
    # scigrid_de has cyclic_state_of_charge=False; converter requires True
    network.storage_units["cyclic_state_of_charge"] = True

    PyPSAStudyConverter(network, current_dir / "tmp" / "scigrid_de", ".tsv").to_gems_study()
    network.optimize()
    assert math.isclose(
        network.objective + network.objective_constant, get_gems_study_objective("scigrid_de"), rel_tol=1e-6
    )


def test_transformer_extendable_modular() -> None:
    """HV-LV network with a MILP transformer and a MILP line."""
    network = Network(name="TransformerExtendableModular", snapshots=list(range(10)))
    network.add("Bus", "A_HV", v_nom=380.0)
    network.add("Bus", "B_LV", v_nom=110.0)
    network.add("Bus", "C_LV", v_nom=110.0)
    network.add("Generator", "G_A", bus="A_HV", p_nom=200, marginal_cost=10)
    network.add("Generator", "G_C", bus="C_LV", p_nom=200, marginal_cost=80)
    network.add("Load", "L_B", bus="B_LV", p_set=[25 + i for i in range(10)], q_set=0)
    network.add("Load", "L_C", bus="C_LV", p_set=[25 + i for i in range(10)], q_set=0)
    network.add(
        "Transformer",
        "T_AB",
        bus0="A_HV",
        bus1="B_LV",
        x=0.20,
        s_nom=50.0,
        s_nom_extendable=True,
        s_nom_mod=50.0,
        s_nom_min=0.0,
        s_nom_max=250.0,
        capital_cost=5.0,
        tap_ratio=1.0,
    )
    network.add(
        "Line",
        "BC",
        bus0="B_LV",
        bus1="C_LV",
        x=0.10,
        s_nom_extendable=True,
        s_nom_mod=30.0,
        s_nom_min=0.0,
        s_nom_max=150.0,
        capital_cost=3.0,
    )

    PyPSAStudyConverter(network, current_dir / "tmp" / "transformer_extendable_modular", ".tsv").to_gems_study()
    network.optimize()
    assert math.isclose(
        network.objective + network.objective_constant,
        get_gems_study_objective("transformer_extendable_modular"),
        rel_tol=1e-6,
    )
