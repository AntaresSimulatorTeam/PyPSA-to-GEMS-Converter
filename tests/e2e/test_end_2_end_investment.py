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

"""PyPSA vs Antares-modeler end-to-end tests for investment studies (extendable generator/link/storage/store)."""

import math

import pytest
from pypsa import Network

from src.pypsa_converter import PyPSAStudyConverter
from tests.e2e.end_2_end_helpers import (
    check_antares_binaries,  # noqa: F401 (autouse fixture)
    current_dir,
    get_gems_study_objective,
    get_original_pypsa_study_objective,
    logger,
)
from tests.utils import load_pypsa_study, preprocess_network

# Disabled for now: the converter requires the 'coin' or 'xpress' solver for investment studies and writes a
# Benders optim-config.yml, which antares-modeler does not solve; these tests run antares-modeler with the default
# solver.
pytestmark = pytest.mark.skip(reason="Investment studies are not yet supported by these antares-modeler e2e tests")


@pytest.mark.parametrize(
    "file, load_scaling, quota, study_name",
    [
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
