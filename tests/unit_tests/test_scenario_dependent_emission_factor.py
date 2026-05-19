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
from pathlib import Path

import pandas as pd  # type: ignore[import-untyped]
import pytest
from pypsa import Network  # type: ignore[import-untyped]

from src.pypsa_converter import PyPSAStudyConverter

logger = logging.getLogger(__name__)


def _build_three_scenario_network() -> Network:
    """
    Three-bus network with one gas generator.  The 'gas' carrier has a
    different co2_emissions value in each of the three scenarios so that
    the converter must write a per-scenario emission_factor file.

    Scenario weights must sum to 1.
    """
    n = Network(name="ScenarioEmissionTest", snapshots=[0, 1, 2])
    n.add("Bus", "bus1", v_nom=1)
    n.add("Carrier", "gas", co2_emissions=0.1)
    n.add(
        "Generator",
        "gas_gen",
        bus="bus1",
        carrier="gas",
        p_nom=100,
        p_nom_extendable=False,
        marginal_cost=30,
    )
    n.add(
        "Generator",
        "wind_gen",
        bus="bus1",
        p_nom=50,
        p_nom_extendable=False,
        marginal_cost=0,
    )
    n.add("Load", "demand", bus="bus1", p_set=80)

    # Create three scenarios with equal weight
    n.set_scenarios({"s1": 1 / 3, "s2": 1 / 3, "s3": 1 / 3})

    # Set scenario-specific co2_emissions on the gas carrier
    n.carriers.loc[("s1", "gas"), "co2_emissions"] = 0.1
    n.carriers.loc[("s2", "gas"), "co2_emissions"] = 0.2
    n.carriers.loc[("s3", "gas"), "co2_emissions"] = 0.5

    return n


def test_scenario_dependent_emission_factor_tsv_created(tmp_path: Path) -> None:
    """Converter writes a per-scenario TSV for emission_factor when co2_emissions
    differs across scenarios."""
    study_dir = tmp_path / "scenario_emission_study"
    network = _build_three_scenario_network()

    PyPSAStudyConverter(
        pypsa_network=network,
        logger=logger,
        study_dir=study_dir,
        series_file_format=".tsv",
    ).to_gems_study()

    # The renamed component is "generator_gas_gen"; pypsa param is "co2_emissions"
    tsv_path = study_dir / "systems" / "input" / "data-series" / "ScenarioEmissionTest_generator_gas_gen_co2_emissions.tsv"
    assert tsv_path.exists(), f"Expected scenario emission_factor TSV at {tsv_path}"

    # TSV is written without a header (include_header=False); the single row IS the data.
    df = pd.read_csv(tsv_path, sep="\t", header=None)
    assert len(df) == 1, "Expected a single data row (static scenario parameter)"
    assert df.shape[1] == 3, f"Expected 3 scenario columns, got {df.shape[1]}"
    values = sorted(df.iloc[0].tolist())
    assert values == pytest.approx(sorted([0.1, 0.2, 0.5]))


def test_uniform_emission_factor_writes_scalar(tmp_path: Path) -> None:
    """When co2_emissions is the same across all scenarios, no scenario TSV is
    written — the value is stored as a scalar in system.yml."""
    n = Network(name="UniformEmission", snapshots=[0, 1])
    n.add("Bus", "bus1", v_nom=1)
    n.add("Carrier", "coal", co2_emissions=0.3)
    n.add("Generator", "coal_gen", bus="bus1", carrier="coal", p_nom=100, p_nom_extendable=False, marginal_cost=50)
    n.add("Load", "demand", bus="bus1", p_set=80)

    n.set_scenarios({"s1": 0.5, "s2": 0.5})
    # co2_emissions is the same in both scenarios (PyPSA replicates the initial value)

    study_dir = tmp_path / "uniform_emission_study"
    PyPSAStudyConverter(
        pypsa_network=n,
        logger=logger,
        study_dir=study_dir,
        series_file_format=".tsv",
    ).to_gems_study()

    series_dir = study_dir / "systems" / "input" / "data-series"
    tsv_files = list(series_dir.glob("*co2_emissions*")) if series_dir.exists() else []
    assert tsv_files == [], f"Expected no scenario emission TSV, found {tsv_files}"
