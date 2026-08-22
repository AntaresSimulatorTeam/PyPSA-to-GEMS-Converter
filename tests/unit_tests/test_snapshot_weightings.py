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
from pathlib import Path
from typing import Any

import pytest
import yaml
from pypsa import Network

from src.pypsa_converter import PyPSAStudyConverter

TIME_WEIGHT_PARAMS = {"hours_per_time_step", "objective_weighting"}


def _build_network(hours_per_time_step: float = 1.0, objective_weighting: float | None = None) -> Network:
    """One bus per storage flavour, covering every model that carries a time-granularity parameter."""
    n = Network(name="GranularityTest", snapshots=[i for i in range(4)])
    n.snapshot_weightings["stores"] = hours_per_time_step
    n.snapshot_weightings["generators"] = hours_per_time_step
    n.snapshot_weightings["objective"] = hours_per_time_step if objective_weighting is None else objective_weighting

    n.add("Bus", "bus1", v_nom=1)
    n.add("Bus", "bus2", v_nom=1)
    n.add("Load", "demand", bus="bus1", p_set=80, q_set=0)
    n.add("Generator", "gen", bus="bus1", p_nom=200, p_nom_extendable=False, marginal_cost=50)
    n.add("Link", "link", bus0="bus1", bus1="bus2", p_nom=50)
    n.add("Store", "store", bus="bus2", e_nom=100, standing_loss=0.05, e_cyclic=True, marginal_cost=10)
    n.add(
        "StorageUnit",
        "storage",
        bus="bus1",
        p_nom=10,
        max_hours=4,
        marginal_cost=10,
        cyclic_state_of_charge=True,
    )
    return n


def _convert(network: Network, study_dir: Path) -> dict[str, Any]:
    PyPSAStudyConverter(
        pypsa_network=network,
        study_dir=study_dir,
        series_file_format=".tsv",
    ).to_gems_study()
    system_yml: dict[str, Any] = yaml.safe_load((study_dir / "systems" / "input" / "system.yml").read_text())
    return system_yml


def _time_weights(system_yml: dict[str, Any], component_id: str) -> dict[str, Any]:
    """Return the time-granularity parameters of one component, keyed by parameter id."""
    (component,) = [c for c in system_yml["system"]["components"] if c["id"] == component_id]
    return {p["id"]: p for p in component["parameters"] if p["id"] in TIME_WEIGHT_PARAMS}


def test_time_weights_written_as_scalars(tmp_path: Path) -> None:
    """A constant 3-hourly granularity is written as a plain value on each component, not as a series."""
    study_dir = tmp_path / "granularity_study"
    system_yml = _convert(_build_network(hours_per_time_step=3.0), study_dir)

    for component_id in ("generator_gen", "store_store", "storage_unit_storage"):
        params = _time_weights(system_yml, component_id)
        assert set(params) == TIME_WEIGHT_PARAMS, f"{component_id} is missing a time-granularity parameter"
        for param in params.values():
            assert param["value"] == 3.0
            assert param["time-dependent"] is False
            assert param["scenario-dependent"] is False

    series_dir = study_dir / "systems" / "input" / "data-series"
    written_series = [path.name for path in series_dir.glob("*")] if series_dir.exists() else []
    assert not [name for name in written_series if any(param in name for param in TIME_WEIGHT_PARAMS)]


def test_objective_weighting_is_independent(tmp_path: Path) -> None:
    """PyPSA allows the objective weighting to differ from the physical time step duration."""
    study_dir = tmp_path / "decoupled_study"
    system_yml = _convert(_build_network(hours_per_time_step=3.0, objective_weighting=156.0), study_dir)

    params = _time_weights(system_yml, "generator_gen")
    assert params["hours_per_time_step"]["value"] == 3.0
    assert params["objective_weighting"]["value"] == 156.0


def test_default_hourly_weightings(tmp_path: Path) -> None:
    """A network with the PyPSA default weightings keeps the hourly values (backward compatibility)."""
    study_dir = tmp_path / "hourly_study"
    system_yml = _convert(_build_network(), study_dir)

    params = _time_weights(system_yml, "generator_gen")
    assert params["hours_per_time_step"]["value"] == 1.0
    assert params["objective_weighting"]["value"] == 1.0


def test_only_models_with_time_dependent_terms_carry_the_parameters(tmp_path: Path) -> None:
    """Links only need the objective weighting; buses and loads need neither."""
    study_dir = tmp_path / "coverage_study"
    system_yml = _convert(_build_network(hours_per_time_step=3.0), study_dir)

    assert set(_time_weights(system_yml, "link_link")) == {"objective_weighting"}
    assert _time_weights(system_yml, "bus1") == {}
    assert _time_weights(system_yml, "load_demand") == {}


def test_time_varying_weightings_are_rejected(tmp_path: Path) -> None:
    """Only constant weightings can be expressed as a scalar GEMS parameter."""
    network = _build_network(hours_per_time_step=3.0)
    network.snapshot_weightings["objective"] = [1.0, 2.0, 3.0, 4.0]

    with pytest.raises(ValueError, match="constant over snapshots"):
        PyPSAStudyConverter(
            pypsa_network=network,
            study_dir=tmp_path / "rejected_study",
            series_file_format=".tsv",
        )

    assert not (tmp_path / "rejected_study").exists(), "no output must be written when the check fails"


def test_mismatched_physical_weightings_are_rejected(tmp_path: Path) -> None:
    """GEMS models a single time step duration, so the two physical columns must agree."""
    network = _build_network(hours_per_time_step=3.0)
    network.snapshot_weightings["generators"] = 1.0

    with pytest.raises(ValueError, match="single time step duration"):
        PyPSAStudyConverter(
            pypsa_network=network,
            study_dir=tmp_path / "rejected_study",
            series_file_format=".tsv",
        )
