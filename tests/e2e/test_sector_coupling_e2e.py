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
"""E2E tests of the PyPSA features needed by sector-coupled PyPSA-Eur networks (issue #126).

Each network is converted, solved by antares-modeler and by PyPSA, and the objectives must match. Every
network is built so that the feature under test is binding (removing it changes PyPSA's optimum).

Single-scenario studies with extendable capacity are routed to Benders by the converter, which antares-modeler
only writes as MPS files. Until single-scenario investment studies are solved by antares-modeler directly
(PR #131), _solve_frontally() rewrites their optim-config.yml so that antares-modeler solves the whole problem
at once, keeping the out-of-bounds processing the converter wrote.
"""

import logging
import math
import subprocess
from collections.abc import Callable
from pathlib import Path
from typing import Any

import numpy as np
import pytest
import yaml
from pypsa import Network

from src.dependencies import get_antares_dir_name, get_antares_modeler_bin
from src.pypsa_converter import PyPSAStudyConverter
from tests.utils import get_objective_value

logger = logging.getLogger(__name__)
current_dir = Path(__file__).resolve().parents[2]

LOAD = [50, 80, 120, 90, 60, 140, 100, 70]


@pytest.fixture(scope="function", autouse=True)
def check_antares_binaries() -> None:
    if not (current_dir / get_antares_dir_name()).is_dir():
        pytest.skip("Antares binaries not found.")


def _solve_frontally(systems: Path) -> None:
    """Replace a Benders optim-config.yml by a single-block resolution of the whole problem (see module docstring)."""
    path = systems / "input" / "optim-config.yml"
    if not path.exists():
        return
    config: dict[str, Any] = yaml.safe_load(path.read_text())
    if config.get("resolution-mode") != "benders-decomposition":
        return
    config["resolution-mode"] = "sequential-subproblems"
    config["models"] = [
        {"id": m["id"], "model-decomposition": {}, "out-of-bounds-processing": m["out-of-bounds-processing"]}
        for m in config.get("models", [])
        if "out-of-bounds-processing" in m
    ]
    path.write_text(yaml.safe_dump(config, sort_keys=False))
    parameters = systems / "parameters.yml"
    parameters.write_text(parameters.read_text().replace("solver: coin", "solver: highs"))


def _gems_objective(systems: Path) -> float:
    modeler = get_antares_modeler_bin(current_dir)
    result = subprocess.run(
        [str(modeler), str(systems)], capture_output=True, text=True, check=False, cwd=str(modeler.parent)
    )
    logger.info("antares-modeler returncode=%s\n%s\n%s", result.returncode, result.stdout, result.stderr)
    result_file = next((systems / "output").glob("**/simulation*"), None)
    if result_file is None:
        raise FileNotFoundError(f"No simulation table under {systems / 'output'}")
    return get_objective_value(result_file)


def _assert_same_objective(
    network: Network, study_name: str, extra_functionality: Callable[[Network, Any], None] | None = None
) -> None:
    extendable = any(
        bool(getattr(network, component)[flag].any())
        for component, flag in (
            ("generators", "p_nom_extendable"),
            ("links", "p_nom_extendable"),
            ("processes", "p_nom_extendable"),
            ("storage_units", "p_nom_extendable"),
            ("stores", "e_nom_extendable"),
        )
        if len(getattr(network, component)) > 0
    )
    study_dir = current_dir / "tmp" / study_name
    # Convert first: the converter must not receive an optimized network (non-copyable solver state).
    PyPSAStudyConverter(network, study_dir, ".tsv", solver_name="coin" if extendable else "highs").to_gems_study()
    _solve_frontally(study_dir / "systems")
    network.optimize(solver_name="highs", extra_functionality=extra_functionality)
    expected = float(network.objective or 0.0) + float(network.objective_constant or 0.0)
    assert math.isclose(expected, _gems_objective(study_dir / "systems"), rel_tol=1e-6)


def _base(name: str) -> Network:
    n = Network(name=name, snapshots=range(len(LOAD)))
    n.add("Carrier", ["gas", "co2", "AC", "DC", "heat", "H2", "biomass", "wind"])
    n.carriers.loc["gas", "co2_emissions"] = 0.2
    n.carriers.loc["co2", "co2_emissions"] = -1.0
    n.add("Bus", "elec", carrier="AC")
    n.add("Load", "load", bus="elec", p_set=LOAD)
    return n


def test_multiport_link() -> None:
    """CHP with four buses, a time-varying efficiency2 and the CO2 output on an atmosphere bus."""
    n = _base("multiport")
    n.add("Bus", "gas", carrier="gas")
    n.add("Bus", "heat", carrier="heat")
    n.add("Bus", "co2 atm", carrier="co2")
    n.add("Generator", "gas supply", bus="gas", p_nom=1000, marginal_cost=20)
    n.add("Generator", "backup", bus="elec", p_nom=1000, marginal_cost=500)
    n.add("Load", "heat load", bus="heat", p_set=[30, 40, 50, 60, 50, 40, 30, 20])
    n.add("Generator", "heat backup", bus="heat", p_nom=1000, marginal_cost=300)
    n.add(
        "Link",
        "CHP",
        bus0="gas",
        bus1="elec",
        bus2="heat",
        bus3="co2 atm",
        efficiency=0.4,
        efficiency2=[0.4, 0.45, 0.5, 0.4, 0.4, 0.45, 0.5, 0.4],
        efficiency3=0.2,
        p_nom=300,
        marginal_cost=2,
    )
    n.add("Store", "co2 atm", bus="co2 atm", e_nom=1e6, e_min_pu=-1)
    n.add("Link", "heat pump", bus0="elec", bus1="heat", efficiency=[3, 3.2, 2.8, 3, 3, 3.1, 2.9, 3], p_nom=10)
    _assert_same_objective(n, "sector_multiport_link")


def test_process() -> None:
    n = _base("process")
    n.add("Bus", "h2", carrier="H2")
    n.add("Bus", "heat", carrier="heat")
    n.add("Generator", "wind", bus="elec", p_nom=200, p_max_pu=[0.9, 0.1, 0.8, 0.2, 1, 0.3, 0.6, 0.5])
    n.add("Generator", "backup", bus="elec", p_nom=1000, marginal_cost=100)
    n.add("Load", "h2 load", bus="h2", p_set=20)
    n.add("Generator", "h2 import", bus="h2", p_nom=1000, marginal_cost=150)
    n.add("Load", "heat load", bus="heat", p_set=5)
    n.add("Generator", "heat backup", bus="heat", p_nom=1000, marginal_cost=80)
    n.add(
        "Process",
        "electrolysis",
        bus0="elec",
        bus1="h2",
        bus2="heat",
        rate0=-1.0,
        rate1=0.7,
        rate2=[0.1, 0.1, 0.2, 0.2, 0.1, 0.1, 0.2, 0.2],
        p_nom=60,
        marginal_cost=1,
    )
    _assert_same_objective(n, "sector_process")


def test_non_cyclic_store() -> None:
    n = _base("ncstore")
    n.add("Generator", "gen", bus="elec", p_nom=150, marginal_cost=[10, 50, 10, 50, 10, 80, 60, 10])
    n.add("Store", "tank", bus="elec", e_nom=200, e_initial=80, standing_loss=0.02, e_cyclic=False, marginal_cost=1)
    n.add("Store", "tank cyclic", bus="elec", e_nom=50, standing_loss=0.01, e_cyclic=True, marginal_cost_storage=0.1)
    _assert_same_objective(n, "sector_non_cyclic_store")


def test_non_cyclic_storage_unit() -> None:
    n = _base("ncsu")
    n.add("Generator", "gen", bus="elec", p_nom=150, marginal_cost=[10, 50, 10, 50, 10, 80, 60, 10])
    n.add(
        "StorageUnit",
        "hydro",
        bus="elec",
        p_nom=40,
        max_hours=5,
        state_of_charge_initial=100,
        cyclic_state_of_charge=False,
        inflow=[5, 0, 10, 0, 5, 20, 0, 0],
        spill_cost=1,
        efficiency_store=0.9,
        efficiency_dispatch=0.85,
        standing_loss=0.01,
        p_min_pu=-0.5,
    )
    n.add("StorageUnit", "phs", bus="elec", p_nom=20, max_hours=4, cyclic_state_of_charge=True, p_min_pu=-0.3)
    _assert_same_objective(n, "sector_non_cyclic_storage_unit")


def test_ramp_limits() -> None:
    """Generator ramps from p_init, a time-varying ramp limit, an extendable ramped generator and a ramped link."""
    n = _base("ramps")
    n.add(
        "Generator",
        "nuclear",
        bus="elec",
        p_nom=100,
        marginal_cost=5,
        ramp_limit_up=0.2,
        ramp_limit_down=0.3,
        p_init=60,
    )
    n.add("Generator", "coal", bus="elec", p_nom=80, marginal_cost=20, ramp_limit_up=[0.5] * 4 + [0.25] * 4)
    n.add(
        "Generator", "ccgt", bus="elec", p_nom_extendable=True, capital_cost=50, marginal_cost=40, ramp_limit_down=0.4
    )
    n.add("Generator", "peak", bus="elec", p_nom=500, marginal_cost=300)
    n.add("Bus", "other", carrier="AC")
    n.add("Generator", "other gen", bus="other", p_nom=200, marginal_cost=1)
    n.add(
        "Link", "interco", bus0="other", bus1="elec", p_nom=100, efficiency=0.95, ramp_limit_up=0.3, ramp_limit_down=0.3
    )
    _assert_same_objective(n, "sector_ramp_limits")


@pytest.mark.parametrize(
    "objective, stores, generators, study_name",
    [
        ([1, 2, 3, 1, 2, 3, 2, 1], [1, 2, 3, 1, 2, 3, 2, 1], [1, 2, 3, 1, 2, 3, 2, 1], "sector_varying_weightings"),
        (2.0, [1, 2, 3, 1, 2, 3, 2, 1], 3.0, "sector_decoupled_weightings"),
    ],
)
def test_time_varying_snapshot_weightings(objective: Any, stores: Any, generators: Any, study_name: str) -> None:
    """Temporal clustering: weightings that vary over snapshots and differ between columns."""
    n = _base("weights")
    n.snapshot_weightings["objective"] = objective
    n.snapshot_weightings["stores"] = stores
    n.snapshot_weightings["generators"] = generators
    n.add("Generator", "gen", bus="elec", p_nom=150, marginal_cost=[10, 50, 10, 50, 10, 80, 60, 10], e_sum_max=1500)
    n.add("Generator", "peak", bus="elec", p_nom=200, marginal_cost=200)
    n.add("Store", "tank", bus="elec", e_nom=300, standing_loss=[0.01, 0.02] * 4, e_cyclic=False, e_initial=50)
    n.add("StorageUnit", "su", bus="elec", p_nom=30, max_hours=6, cyclic_state_of_charge=True, standing_loss=0.01)
    _assert_same_objective(n, study_name)


def test_primary_energy_with_storage_depletion() -> None:
    """A non-cyclic gas store contributes (initial - final level) * co2_emissions, and a >= bound."""
    n = _base("primary")
    n.add("Bus", "gas", carrier="gas")
    n.add("Store", "gas reservoir", bus="gas", carrier="gas", e_nom=2000, e_initial=1000, e_cyclic=False)
    n.add("Link", "ocgt", bus0="gas", bus1="elec", p_nom=200, efficiency=0.5)
    n.add("Generator", "gas gen", bus="elec", carrier="gas", p_nom=60, marginal_cost=10, efficiency=0.5)
    n.add("Generator", "clean", bus="elec", p_nom=200, marginal_cost=90)
    n.add("GlobalConstraint", "co2 limit", type="primary_energy", sense="<=", constant=150)
    n.add("GlobalConstraint", "co2 floor", type="primary_energy", sense=">=", constant=20)
    _assert_same_objective(n, "sector_primary_energy_storage")


def test_operational_limit() -> None:
    n = _base("oplimit")
    n.add("Generator", "bio", bus="elec", carrier="biomass", p_nom=100, marginal_cost=5)
    n.add("Generator", "peak", bus="elec", p_nom=200, marginal_cost=100)
    n.add("Bus", "bio store", carrier="biomass")
    n.add("Store", "bio stock", bus="bio store", carrier="biomass", e_nom=300, e_initial=200, e_cyclic=False)
    n.add("Link", "bio plant", bus0="bio store", bus1="elec", p_nom=50, efficiency=0.4)
    n.add(
        "GlobalConstraint",
        "biomass limit",
        type="operational_limit",
        carrier_attribute="biomass",
        sense="<=",
        constant=250,
    )
    _assert_same_objective(n, "sector_operational_limit")


def _co2_atmosphere_constraint(n: Network, snapshots: Any) -> None:
    """PyPSA-Eur's add_co2_atmosphere_constraint (scripts/solve_network.py), which plain n.optimize() lacks."""
    for name, glc in n.global_constraints.query('type == "co2_atmosphere"').iterrows():
        emissions = n.carriers.query(f"{glc.carrier_attribute} != 0")[glc.carrier_attribute]
        stores = n.stores[n.stores.bus.map(n.buses.carrier).isin(emissions.index) & ~n.stores.e_cyclic]
        lhs = n.model["Store-e"].loc[snapshots[-1], stores.index].sum()
        n.model.add_constraints(lhs, {"<=": "<=", "==": "=", ">=": ">="}[glc.sense], glc.constant, name=f"GC-{name}")


def test_co2_atmosphere() -> None:
    """PyPSA-Eur sector-coupled CO2 limit: final level of the CO2 atmosphere store (e_nom = inf)."""
    n = _base("co2atm")
    n.add("Bus", "gas", carrier="gas")
    n.add("Bus", "co2 atmosphere", carrier="co2")
    n.add("Generator", "gas supply", bus="gas", p_nom=1000, marginal_cost=20)
    n.add("Link", "OCGT", bus0="gas", bus1="elec", bus2="co2 atmosphere", efficiency=0.4, efficiency2=0.2, p_nom=300)
    n.add(
        "Generator",
        "wind",
        bus="elec",
        carrier="wind",
        p_nom_extendable=True,
        capital_cost=200,
        p_max_pu=[0.5, 0.2, 0.9, 0.3, 0.6, 0.1, 0.4, 0.8],
    )
    n.add("Store", "co2 atmosphere", bus="co2 atmosphere", carrier="co2", e_nom=np.inf, e_min_pu=-1)
    n.add("Store", "battery", bus="elec", e_nom_extendable=True, capital_cost=10, e_cyclic=True)
    n.add(
        "GlobalConstraint",
        "CO2Limit",
        type="co2_atmosphere",
        carrier_attribute="co2_emissions",
        sense="<=",
        constant=60,
    )
    _assert_same_objective(n, "sector_co2_atmosphere", extra_functionality=_co2_atmosphere_constraint)


@pytest.mark.parametrize("kind", ["volume", "cost"])
def test_transmission_expansion_limit(kind: str) -> None:
    n = _base(f"tx_{kind}")
    n.add("Bus", ["b2", "b3"], carrier="AC")
    n.add("Generator", "cheap", bus="b2", p_nom=500, marginal_cost=5)
    n.add("Generator", "mid", bus="b3", p_nom=500, marginal_cost=30)
    n.add("Generator", "dear", bus="elec", p_nom=500, marginal_cost=100)
    line_args: dict[str, Any] = {"s_nom": 20, "s_nom_extendable": True, "s_nom_min": 20}
    n.add("Line", "l1", bus0="b2", bus1="elec", x=0.1, capital_cost=3, length=100, **line_args)
    n.add("Line", "l2", bus0="b3", bus1="elec", x=0.2, capital_cost=2, length=50, **line_args)
    n.add(
        "Link",
        "dc",
        bus0="b2",
        bus1="b3",
        carrier="DC",
        p_nom=10,
        p_nom_extendable=True,
        p_min_pu=-1,
        capital_cost=4,
        length=80,
    )
    reference = 100 * 20 + 50 * 20 + 80 * 10 if kind == "volume" else 3 * 20 + 2 * 20 + 4 * 10
    constraint_type = "transmission_volume_expansion_limit" if kind == "volume" else "transmission_expansion_cost_limit"
    n.add(
        "GlobalConstraint",
        "limit",
        type=constraint_type,
        sense="<=",
        constant=1.5 * reference,
        carrier_attribute="AC, DC",
    )
    _assert_same_objective(n, f"sector_transmission_{kind}_limit")


def test_tech_capacity_expansion_limit() -> None:
    n = _base("techcap")
    n.add("Bus", "b2", carrier="AC")
    n.add("Line", "l", bus0="b2", bus1="elec", x=0.1, s_nom=1000)
    n.add("Generator", "wind1", bus="elec", carrier="wind", p_nom_extendable=True, capital_cost=100, p_max_pu=0.4)
    n.add("Generator", "wind2", bus="b2", carrier="wind", p_nom_extendable=True, capital_cost=120, p_max_pu=0.5)
    n.add("Generator", "peak", bus="elec", p_nom=500, marginal_cost=50)
    n.add(
        "GlobalConstraint",
        "wind cap",
        type="tech_capacity_expansion_limit",
        carrier_attribute="wind",
        sense="<=",
        constant=150,
    )
    n.add(
        "GlobalConstraint",
        "wind b2",
        type="tech_capacity_expansion_limit",
        carrier_attribute="wind",
        sense=">=",
        constant=40,
        bus="b2",
    )
    _assert_same_objective(n, "sector_tech_capacity_limit")


def test_bus_carrier_capacity_limit() -> None:
    n = _base("buscarrier")
    n.add("Generator", "wind1", bus="elec", carrier="wind", p_nom_extendable=True, capital_cost=100, p_max_pu=0.4)
    n.add("Generator", "wind2", bus="elec", carrier="wind", p_nom_extendable=True, capital_cost=220, p_max_pu=0.6)
    n.add("Generator", "peak", bus="elec", p_nom=500, marginal_cost=50)
    n.buses["nom_max_wind"] = 120.0
    n.buses["nom_min_wind"] = 60.0
    _assert_same_objective(n, "sector_bus_carrier_limit")


def test_nominal_set_inactive_and_periodized_costs() -> None:
    """p_nom_set pins an extendable generator, overnight_cost/fom_cost are periodized, an inactive generator is
    dropped, and a storage unit charges up to -p_min_pu * p_nom."""
    n = _base("misc")
    n.add(
        "Generator",
        "pinned",
        bus="elec",
        p_nom_extendable=True,
        p_nom_set=70,
        p_nom_max=500,
        capital_cost=10,
        marginal_cost=5,
    )
    n.add(
        "Generator",
        "overnight",
        bus="elec",
        p_nom_extendable=True,
        overnight_cost=1000,
        discount_rate=0.07,
        lifetime=25,
        fom_cost=3,
        marginal_cost=20,
    )
    n.add("Generator", "inactive cheap", bus="elec", p_nom=500, marginal_cost=0, active=False)
    n.add("Generator", "peak", bus="elec", p_nom=500, marginal_cost=200)
    n.add(
        "StorageUnit",
        "asym",
        bus="elec",
        p_nom=30,
        max_hours=3,
        cyclic_state_of_charge=True,
        p_min_pu=-0.2,
        p_max_pu=0.9,
    )
    _assert_same_objective(n, "sector_nom_set_inactive_periodized")
