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
"""The converter rejects PyPSA features it doesn't model, and accepts networks left at PyPSA's defaults."""

import logging
from collections.abc import Callable
from pathlib import Path

import pandas as pd
import pytest
from pypsa import Network

from src.pypsa_converter import PyPSAStudyConverter

N_SNAPSHOTS = 4


def _network() -> Network:
    """One component of every type the checks look at, all at PyPSA's defaults."""
    n = Network(name="UnsupportedFeatures", snapshots=range(N_SNAPSHOTS))
    n.add("Bus", "a", v_nom=1)
    n.add("Bus", "b", v_nom=1)
    n.add("Bus", "c", v_nom=1)
    n.add("Load", "load", bus="a", p_set=10)
    n.add("Generator", "gen", bus="a", p_nom=100, marginal_cost=10)
    n.add("Link", "link", bus0="a", bus1="c", p_nom=50)
    n.add("StorageUnit", "su", bus="a", p_nom=10, max_hours=2, cyclic_state_of_charge=True)
    n.add("Store", "store", bus="c", e_nom=10, e_cyclic=True)
    n.add("Line", "line", bus0="a", bus1="b", x=0.1, s_nom=100)
    n.add("Transformer", "trafo", bus0="b", bus1="c", x=0.1, s_nom=100)
    return n


def _convert(network: Network, tmp_path: Path) -> None:
    PyPSAStudyConverter(pypsa_network=network, study_dir=tmp_path / "study", series_file_format=".tsv")


def _set(attr: str, value: object, component: str = "Generator", name: str = "gen") -> Callable[[Network], None]:
    def apply(n: Network) -> None:
        n.static(component).loc[name, attr] = value

    return apply


def _add_bus2(n: Network) -> None:
    n.add("Link", "chp", bus0="a", bus1="b", bus2="c", p_nom=10, efficiency2=0.5)


def _ramp_series(n: Network) -> None:
    n.dynamic("Generator")["ramp_limit_up"] = pd.DataFrame({"gen": [0.5] * N_SNAPSHOTS}, index=n.snapshots)


def _piecewise(n: Network) -> None:
    # Piecewise shorthand {p_pu: marginal_cost} (PyPSA >= 1.3.0).
    n.add("Generator", "pw", bus="a", p_nom=10, marginal_cost={0.0: 5.0, 1.0: 10.0})


def _process(n: Network) -> None:
    n.add("Process", "proc", bus0="a", bus1="b", p_nom=10)


def _bus_carrier_limit(n: Network) -> None:
    n.add("Carrier", "solar")
    n.buses["nom_max_solar"] = [100.0, float("nan"), float("nan")]


ERROR_CASES: list[tuple[str, Callable[[Network], None], str]] = [
    ("process", _process, "Process components"),
    ("link-bus2", _add_bus2, "Links with bus2"),
    ("phase-shift", _set("phase_shift", 10.0, "Transformer", "trafo"), "Transformers with phase_shift"),
    ("phase-shift-range", _set("phase_shift_max", 30.0, "Transformer", "trafo"), "phase_shift_min < phase_shift_max"),
    ("v-ang-max-line", _set("v_ang_max", 30.0, "Line", "line"), "Lines with v_ang_max"),
    ("v-ang-max-trafo", _set("v_ang_max", 30.0, "Transformer", "trafo"), "Transformers with v_ang_max"),
    ("p-nom-set", _set("p_nom_set", 50.0), "Generators with p_nom_set"),
    ("p-nom-set-link", _set("p_nom_set", 50.0, "Link", "link"), "Links with p_nom_set"),
    ("p-nom-set-su", _set("p_nom_set", 5.0, "StorageUnit", "su"), "Storage Units with p_nom_set"),
    ("e-nom-set", _set("e_nom_set", 5.0, "Store", "store"), "Stores with e_nom_set"),
    ("s-nom-set", _set("s_nom_set", 50.0, "Line", "line"), "Lines with s_nom_set"),
    ("delay", _set("delay", 1, "Link", "link"), "Links with delay"),
    ("maintainable", _set("maintainable", True), "Generators with maintainable"),
    ("ramp-up", _set("ramp_limit_up", 0.5), "Generators with ramp_limit_up"),
    ("ramp-down-link", _set("ramp_limit_down", 0.5, "Link", "link"), "Links with ramp_limit_down"),
    ("ramp-up-series", _ramp_series, "Generators with ramp_limit_up"),
    ("p-nom-mod", _set("p_nom_mod", 10.0), "Generators with p_nom_mod"),
    ("e-nom-mod", _set("e_nom_mod", 5.0, "Store", "store"), "Stores with e_nom_mod"),
    ("fom-cost", _set("fom_cost", 3.0), "Generators with fom_cost"),
    ("fom-cost-line", _set("fom_cost", 3.0, "Line", "line"), "Lines with fom_cost"),
    ("overnight-cost", _set("overnight_cost", 1000.0, "Link", "link"), "Links with overnight_cost"),
    ("soc-set", _set("state_of_charge_set", 5.0, "StorageUnit", "su"), "Storage Units with state_of_charge_set"),
    ("p-dispatch-set", _set("p_dispatch_set", 1.0, "StorageUnit", "su"), "Storage Units with p_dispatch_set"),
    ("p-store-set", _set("p_store_set", 1.0, "StorageUnit", "su"), "Storage Units with p_store_set"),
    ("e-set", _set("e_set", 1.0, "Store", "store"), "Stores with e_set"),
    ("bus-carrier-limit", _bus_carrier_limit, "Buses with nom_max_solar"),
    ("inactive-line", _set("active", False, "Line", "line"), "Lines with active = 1"),
    ("inactive-trafo", _set("active", False, "Transformer", "trafo"), "Transformers with active = 1"),
    ("inactive-su", _set("active", False, "StorageUnit", "su"), "Storage Units with active = 1"),
    ("inactive-store", _set("active", False, "Store", "store"), "Stores with active = 1"),
]


def test_network_at_pypsa_defaults_is_accepted(tmp_path: Path, caplog: pytest.LogCaptureFixture) -> None:
    with caplog.at_level(logging.WARNING, logger="src.unsupported_features"):
        _convert(_network(), tmp_path)
    assert not caplog.records


@pytest.mark.parametrize(("modify", "match"), [case[1:] for case in ERROR_CASES], ids=[c[0] for c in ERROR_CASES])
def test_unsupported_feature_is_rejected(modify: Callable[[Network], None], match: str, tmp_path: Path) -> None:
    network = _network()
    modify(network)
    with pytest.raises(ValueError, match=match):
        _convert(network, tmp_path)
    assert not (tmp_path / "study").exists(), "no output must be written when the check fails"


def test_all_unsupported_features_are_reported_together(tmp_path: Path) -> None:
    network = _network()
    _set("ramp_limit_up", 0.5)(network)
    _set("delay", 2, "Link", "link")(network)
    with pytest.raises(ValueError) as error:
        _convert(network, tmp_path)
    assert "Generators with ramp_limit_up" in str(error.value)
    assert "Links with delay" in str(error.value)


def test_unsupported_feature_is_detected_in_a_multi_scenario_network(tmp_path: Path) -> None:
    network = _network()
    network.set_scenarios({"low": 0.5, "high": 0.5})
    network.generators.loc[("high", "gen"), "ramp_limit_up"] = 0.5
    with pytest.raises(ValueError, match="Generators with ramp_limit_up .*: gen"):
        _convert(network, tmp_path)


@pytest.mark.parametrize(
    ("modify", "message"),
    [
        (_set("v_ang_min", -30.0, "Line", "line"), "Lines with v_ang_min"),
        (_set("p_init", 20.0), "Generators with p_init"),
    ],
    ids=["v-ang-min", "p-init"],
)
def test_ignored_feature_is_a_warning(
    modify: Callable[[Network], None], message: str, tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    network = _network()
    modify(network)
    with caplog.at_level(logging.WARNING, logger="src.unsupported_features"):
        _convert(network, tmp_path)
    assert any(message in record.getMessage() for record in caplog.records)


def test_piecewise_curves_are_rejected_by_pypsa(tmp_path: Path) -> None:
    """The converter adds a "default" scenario, which PyPSA refuses on a network with piecewise data."""
    network = _network()
    _piecewise(network)
    with pytest.raises(NotImplementedError, match="piecewise"):
        _convert(network, tmp_path)


def test_investment_periods_are_rejected(tmp_path: Path) -> None:
    network = _network()
    network.snapshots = pd.MultiIndex.from_product([[2030], range(N_SNAPSHOTS)], names=["period", "timestep"])
    network.investment_periods = pd.Index([2030])
    with pytest.raises(ValueError, match="without investment periods"):
        _convert(network, tmp_path)


def test_snapshot_weightings_other_than_one_are_rejected(tmp_path: Path) -> None:
    network = _network()
    network.snapshot_weightings["objective"] = 2.0
    with pytest.raises(ValueError, match="snapshot weightings equal to 1.0"):
        _convert(network, tmp_path)


@pytest.mark.parametrize(
    ("attrs", "match"),
    [
        ({"type": "operational_limit", "carrier_attribute": "co2_emissions"}, "type 'primary_energy'"),
        ({"type": "primary_energy", "carrier_attribute": "nox"}, "carrier attribute 'co2_emissions'"),
    ],
    ids=["type", "carrier-attribute"],
)
def test_unsupported_global_constraint_is_rejected(attrs: dict[str, str], match: str, tmp_path: Path) -> None:
    network = _network()
    network.add(
        "GlobalConstraint",
        "limit",
        type=attrs["type"],
        carrier_attribute=attrs["carrier_attribute"],
        sense="<=",
        constant=100.0,
    )
    with pytest.raises(ValueError, match=match):
        _convert(network, tmp_path)
