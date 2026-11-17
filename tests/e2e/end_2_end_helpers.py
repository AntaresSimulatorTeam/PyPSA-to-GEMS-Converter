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

"""Shared helpers for the PyPSA vs Antares-modeler end-to-end tests (test_end_2_end_*.py)."""

import logging
import math
import subprocess
from pathlib import Path

import pandas as pd
import pytest
from pypsa import Network

from src.dependencies import get_antares_dir_name, get_antares_modeler_bin
from tests.utils import get_objective_value

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


# Component types with a capacity variable: (GEMS variable, PyPSA "*_extendable" column, PyPSA optimized capacity).
_CAPACITY_COLUMNS = {
    "generators": ("p_nom", "p_nom_extendable", "p_nom_opt"),
    "links": ("p_nom", "p_nom_extendable", "p_nom_opt"),
    "storage_units": ("p_nom", "p_nom_extendable", "p_nom_opt"),
    "stores": ("e_nom", "e_nom_extendable", "e_nom_opt"),
}


def assert_capacities_match(network: Network, study_name: str, rel_tol: float = 1e-6, abs_tol: float = 1e-6) -> None:
    """Compare the capacities of the extendable components of an optimized network with antares-modeler's."""
    result_file = next((current_dir / "tmp" / study_name / "systems" / "output").glob("**/simulation[-_]table*"))
    table = pd.read_csv(result_file, sep="\t" if result_file.suffix == ".tsv" else ",")
    capacities = table[table["output"].isin(["p_nom", "e_nom"])]
    gems = {
        (str(component), str(output)): float(value)
        for component, output, value in zip(capacities["component"], capacities["output"], capacities["value"])
    }
    n_compared = 0
    for component_type, (variable, extendable_col, optimized_col) in _CAPACITY_COLUMNS.items():
        df = getattr(network, component_type)
        for name in df.index[df[extendable_col].astype(bool)]:
            gems_id = f"{component_type[:-1]}_{str(name).replace(' ', '_')}"
            expected = float(df.at[name, optimized_col])
            actual = gems[(gems_id, variable)]
            assert math.isclose(actual, expected, rel_tol=rel_tol, abs_tol=abs_tol), (
                f"{gems_id}.{variable}: antares-modeler {actual} vs PyPSA {expected}"
            )
            n_compared += 1
    assert n_compared > 0, "No extendable component to compare"
    logger.info("Capacities match PyPSA for %s (%s components)", study_name, n_compared)


def get_gems_study_objective(study_name: str) -> float:
    study_dir = current_dir / "tmp" / study_name

    antares_modeler_bin = get_antares_modeler_bin(current_dir)

    logger.info(f"Running Antares modeler with study directory: {study_dir / 'systems'}")

    result = subprocess.run(
        [str(antares_modeler_bin), str(study_dir / "systems")],
        capture_output=True,
        text=True,
        check=False,
        cwd=str(antares_modeler_bin.parent),
    )
    logger.info("================================")
    logger.info("Antares modeler output: returncode=%s", result.returncode)
    logger.info("stdout: %s", result.stdout)
    logger.info("stderr: %s", result.stderr)
    logger.info("================================")

    logger.info("Getting Antares study objective")

    output_dir = study_dir / "systems" / "output"
    # Antares writes the result into a timestamped run subfolder: output/<timestamp>/simulation_table.csv (10.1)
    # or output/<timestamp>/simulation-table-<scenario>.csv (>= 10.2).
    result_file = next(output_dir.glob("**/simulation[-_]table*"), None)

    if result_file is not None:
        obj = get_objective_value(result_file)
        logger.info("GEMS study objective for %s: %s", study_name, obj)
        return obj

    raise FileNotFoundError(f"Result file not found in {output_dir}")
