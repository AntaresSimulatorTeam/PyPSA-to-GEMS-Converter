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
"""Structural mapping of the PyPSA features needed by sector-coupled PyPSA-Eur networks (issue #126):
model routing, parameters, port connections, shared data series and optim-config.yml."""

from pathlib import Path
from typing import Any, cast

import numpy as np
import pytest
import yaml
from pypsa import Network

from src.pypsa_converter import PyPSAStudyConverter

LIBRARY = Path(__file__).resolve().parents[2] / "resources" / "pypsa_models" / "pypsa_models.yml"
N_SNAPSHOTS = 4


def _sector_network() -> Network:
    """One component of every new GEMS model, plus every supported global constraint type."""
    n = Network(name="Sector", snapshots=range(N_SNAPSHOTS))
    n.add("Carrier", "gas", co2_emissions=0.2)
    n.add("Carrier", "co2", co2_emissions=-1.0)
    n.add("Carrier", ["AC", "DC", "heat", "H2", "biomass", "wind"])
    n.add("Bus", "elec", carrier="AC")
    n.add("Bus", "elec2", carrier="AC")
    n.add("Bus", "gas", carrier="gas")
    n.add("Bus", "heat", carrier="heat")
    n.add("Bus", "h2", carrier="H2")
    n.add("Bus", "co2 atmosphere", carrier="co2")
    n.add("Load", "load", bus="elec", p_set=[50, 80, 120, 90])
    n.add("Load", "process emissions", bus="co2 atmosphere", p_set=-5.0)
    n.add("Generator", "gas supply", bus="gas", carrier="gas", p_nom=1000, marginal_cost=20)
    n.add("Generator", "wind", bus="elec", carrier="wind", p_nom_extendable=True, capital_cost=100, p_max_pu=0.4)
    n.add("Generator", "nuclear", bus="elec", p_nom=100, marginal_cost=5, ramp_limit_up=0.2, p_init=50)
    n.add("Generator", "bio", bus="elec", carrier="biomass", p_nom=30, marginal_cost=3)
    n.add("Line", "ac", bus0="elec", bus1="elec2", x=0.1, s_nom=50, s_nom_extendable=True, capital_cost=1, length=10)
    n.add("Link", "dc", bus0="elec", bus1="elec2", carrier="DC", p_nom_extendable=True, capital_cost=2, length=20)
    n.add("Link", "ramped", bus0="elec2", bus1="elec", p_nom=10, ramp_limit_down=0.5)
    n.add(
        "Link",
        "CHP",
        bus0="gas",
        bus1="elec",
        bus2="heat",
        bus3="co2 atmosphere",
        efficiency=0.4,
        efficiency2=[0.4, 0.5, 0.4, 0.5],
        efficiency3=0.2,
        p_nom=300,
    )
    n.add("Process", "electrolysis", bus0="elec", bus1="h2", rate0=-1.0, rate1=0.7, p_nom=10)
    n.add("Store", "co2 atmosphere", bus="co2 atmosphere", carrier="co2", e_nom=np.inf, e_min_pu=-1)
    n.add("Store", "h2 tank", bus="h2", e_nom=100, e_cyclic=True, standing_loss=0.01)
    n.add("Store", "biomass stock", bus="elec", carrier="biomass", e_nom=100, e_initial=50, e_cyclic=False)
    n.add("StorageUnit", "hydro", bus="elec", p_nom=20, max_hours=6, state_of_charge_initial=40, inflow=2)
    n.add("StorageUnit", "phs", bus="elec", p_nom=20, max_hours=6, cyclic_state_of_charge=True, p_min_pu=-0.5)
    n.add("Load", "heat load", bus="heat", p_set=10)
    n.add("Generator", "heat backup", bus="heat", p_nom=100, marginal_cost=100)
    n.add("Load", "h2 load", bus="h2", p_set=1)
    n.add(
        "GlobalConstraint",
        "CO2Limit",
        type="co2_atmosphere",
        carrier_attribute="co2_emissions",
        sense="<=",
        constant=50,
    )
    n.add("GlobalConstraint", "co2 floor", type="primary_energy", sense=">=", constant=-1000)
    n.add(
        "GlobalConstraint",
        "biomass limit",
        type="operational_limit",
        carrier_attribute="biomass",
        sense="<=",
        constant=200,
    )
    n.add(
        "GlobalConstraint",
        "lv_limit",
        type="transmission_volume_expansion_limit",
        carrier_attribute="AC, DC",
        sense="<=",
        constant=10000,
    )
    n.add(
        "GlobalConstraint",
        "wind cap",
        type="tech_capacity_expansion_limit",
        carrier_attribute="wind",
        sense="<=",
        constant=500,
    )
    return n


@pytest.fixture(scope="module")
def converted(tmp_path_factory: pytest.TempPathFactory) -> tuple[Path, dict[str, Any]]:
    study_dir = tmp_path_factory.mktemp("sector") / "study"
    PyPSAStudyConverter(_sector_network(), study_dir, ".tsv", solver_name="coin").to_gems_study()
    system: dict[str, Any] = yaml.safe_load((study_dir / "systems" / "input" / "system.yml").read_text())["system"]
    return study_dir, system


def _component(system: dict[str, Any], component_id: str) -> dict[str, Any]:
    (component,) = [c for c in system["components"] if c["id"] == component_id]
    return cast(dict[str, Any], component)


def _params(system: dict[str, Any], component_id: str) -> dict[str, Any]:
    return {p["id"]: p["value"] for p in _component(system, component_id).get("parameters", [])}


def _connections_of(system: dict[str, Any], component_id: str) -> set[tuple[str, str, str]]:
    """(own port, other component, other port) for every connection of a component."""
    result = set()
    for c in system["connections"]:
        if c["component1"] == component_id:
            result.add((c["port1"], c["component2"], c["port2"]))
        if c["component2"] == component_id:
            result.add((c["port2"], c["component1"], c["port1"]))
    return result


def test_every_component_carries_exactly_its_model_parameters(converted: tuple[Path, dict[str, Any]]) -> None:
    """AGENTS.md rule 3: a parameter list that differs from the library makes antares-modeler fail silently."""
    _, system = converted
    library = yaml.safe_load(LIBRARY.read_text())["library"]
    model_params = {m["id"]: {p["id"] for p in m.get("parameters", [])} for m in library["models"]}
    model_ports = {m["id"]: {p["id"] for p in m.get("ports", [])} for m in library["models"]}
    used = set()
    for component in system["components"]:
        model_id = component["model"].split(".", 1)[1]
        used.add(model_id)
        assert {p["id"] for p in component.get("parameters", [])} == model_params[model_id], component["id"]
    for c in system["connections"]:
        for side in ("1", "2"):
            component = _component(system, c[f"component{side}"])
            assert c[f"port{side}"] in model_ports[component["model"].split(".", 1)[1]]
    assert {
        "generator_ramp",
        "link_ramp",
        "link_multiport",
        "store_non_cyclic",
        "storage_unit_non_cyclic",
        "global_constraint_quantity_max",
        "global_constraint_capacity_max",
        "global_constraint_co2_min",
    } <= used


def test_multiport_link_connects_only_the_buses_it_has(converted: tuple[Path, dict[str, Any]]) -> None:
    _, system = converted
    assert _component(system, "link_CHP")["model"] == "pypsa_models.link_multiport"
    ports = {port for port, _, other_port in _connections_of(system, "link_CHP") if other_port == "p_balance_port"}
    assert ports == {"p0_port", "p1_port", "p2_port", "p3_port"}
    params = _params(system, "link_CHP")
    assert params["rate0"] == -1.0
    assert params["efficiency3"] == 0.2
    assert params["efficiency4"] == 0.0
    assert params["efficiency2"] == "Sector_link_CHP_efficiency2"  # time-varying
    assert _component(system, "link_dc")["model"] == "pypsa_models.link"


def test_process_maps_its_rates_onto_the_multiport_model(converted: tuple[Path, dict[str, Any]]) -> None:
    _, system = converted
    assert _component(system, "process_electrolysis")["model"] == "pypsa_models.link_multiport"
    params = _params(system, "process_electrolysis")
    assert (params["rate0"], params["efficiency"], params["efficiency2"]) == (-1.0, 0.7, 0.0)


def test_ramp_limits_route_to_ramp_models(converted: tuple[Path, dict[str, Any]]) -> None:
    _, system = converted
    nuclear = _params(system, "generator_nuclear")
    assert _component(system, "generator_nuclear")["model"] == "pypsa_models.generator_ramp"
    assert (nuclear["ramp_limit_up"], nuclear["ramp_up_active"], nuclear["ramp_down_active"]) == (0.2, 1.0, 0.0)
    assert (nuclear["p_init"], nuclear["p_init_active"]) == (50.0, 1.0)
    assert _component(system, "link_ramped")["model"] == "pypsa_models.link_ramp"
    assert _component(system, "generator_bio")["model"] == "pypsa_models.generator"


def test_non_cyclic_storage_uses_the_shared_last_time_step_indicator(converted: tuple[Path, dict[str, Any]]) -> None:
    study_dir, system = converted
    assert _component(system, "store_co2_atmosphere")["model"] == "pypsa_models.store_non_cyclic"
    assert _component(system, "store_h2_tank")["model"] == "pypsa_models.store"
    assert _component(system, "storage_unit_hydro")["model"] == "pypsa_models.storage_unit_non_cyclic"
    assert _component(system, "storage_unit_phs")["model"] == "pypsa_models.storage_unit"
    assert _params(system, "store_biomass_stock")["e_initial"] == 50.0
    assert _params(system, "store_co2_atmosphere")["is_last_time_step"] == "Sector_last_time_step_indicator"
    series = study_dir / "systems" / "input" / "data-series" / "Sector_last_time_step_indicator.tsv"
    assert [float(v) for v in series.read_text().split()] == [0.0] * (N_SNAPSHOTS - 1) + [1.0]
    # A fixed infinite capacity is clamped, so that the fixed e_nom variable has a finite value.
    assert _params(system, "store_co2_atmosphere")["e_nom_max"] == 1e11


def test_global_constraints_connect_the_components_pypsa_sums(converted: tuple[Path, dict[str, Any]]) -> None:
    _, system = converted
    expected = {
        "global_constraint_CO2Limit": (
            "global_constraint_quantity_max",
            {("quantity_port", "store_co2_atmosphere", "final_level_port")},
        ),
        "global_constraint_co2_floor": (
            "global_constraint_co2_min",
            {
                ("emission_port", "generator_gas_supply", "emission_port"),
                ("emission_port", "store_co2_atmosphere", "emission_port"),
            },
        ),
        "global_constraint_biomass_limit": (
            "global_constraint_quantity_max",
            {
                ("quantity_port", "generator_bio", "operational_port"),
                ("quantity_port", "store_biomass_stock", "operational_port"),
            },
        ),
        "global_constraint_lv_limit": (
            "global_constraint_capacity_max",
            {
                ("capacity_port", "line_ac", "expansion_volume_port"),
                ("capacity_port", "link_dc", "expansion_volume_port"),
            },
        ),
        "global_constraint_wind_cap": (
            "global_constraint_capacity_max",
            {("capacity_port", "generator_wind", "capacity_port")},
        ),
    }
    for component_id, (model, connections) in expected.items():
        assert _component(system, component_id)["model"] == f"pypsa_models.{model}"
        assert _connections_of(system, component_id) == connections, component_id


def test_optim_config_drops_the_wrapped_constraints(converted: tuple[Path, dict[str, Any]]) -> None:
    study_dir, _ = converted
    optim_config = yaml.safe_load((study_dir / "systems" / "input" / "optim-config.yml").read_text())
    models = {m["id"].split(".", 1)[1]: m for m in optim_config["models"]}
    dropped = {
        model: [c["id"] for c in entry["out-of-bounds-processing"]["constraints"] if c["mode"] == "drop"]
        for model, entry in models.items()
        if "out-of-bounds-processing" in entry
    }
    assert dropped == {
        "generator_ramp": ["ramp_up", "ramp_down"],
        "link_ramp": ["ramp_up", "ramp_down"],
        "store_non_cyclic": ["energy_balance"],
        "storage_unit_non_cyclic": ["state_of_charge_balance"],
    }
    # Investment study: Benders decomposition of the new models, global capacity constraints in the master.
    assert optim_config["resolution-mode"] == "benders-decomposition"
    assert models["link_multiport"]["model-decomposition"]["variables"] == [
        {"id": "p_nom", "location": "master-and-subproblems"}
    ]
    assert models["global_constraint_capacity_max"]["model-decomposition"]["binding-constraints"] == [
        {"id": "constraint_expression", "location": "master"}
    ]


def test_operational_study_gets_an_optim_config_only_when_needed(tmp_path: Path) -> None:
    n = Network(name="Op", snapshots=range(N_SNAPSHOTS))
    n.add("Bus", "b")
    n.add("Load", "l", bus="b", p_set=10)
    n.add("Generator", "g", bus="b", p_nom=100, marginal_cost=1)
    n.add("Store", "s", bus="b", e_nom=10, e_cyclic=True)
    PyPSAStudyConverter(n.copy(), tmp_path / "cyclic", ".tsv").to_gems_study()
    assert not (tmp_path / "cyclic" / "systems" / "input" / "optim-config.yml").exists()

    n.stores["e_cyclic"] = False
    PyPSAStudyConverter(n, tmp_path / "non_cyclic", ".tsv").to_gems_study()
    optim_config = yaml.safe_load((tmp_path / "non_cyclic" / "systems" / "input" / "optim-config.yml").read_text())
    # No resolution-mode: the file must stay readable by GemsPy as well as antares-modeler.
    assert optim_config == {
        "models": [
            {
                "id": "pypsa_models.store_non_cyclic",
                "model-decomposition": {},
                "out-of-bounds-processing": {"constraints": [{"id": "energy_balance", "mode": "drop"}]},
            }
        ]
    }


def test_nominal_set_pins_extendable_capacity_and_costs_are_periodized(tmp_path: Path) -> None:
    n = Network(name="Costs", snapshots=range(N_SNAPSHOTS))
    n.add("Bus", "b")
    n.add("Load", "l", bus="b", p_set=10)
    n.add("Generator", "pinned", bus="b", p_nom_extendable=True, p_nom_set=70, p_nom_max=500, capital_cost=10)
    n.add(
        "Generator",
        "overnight",
        bus="b",
        p_nom_extendable=True,
        overnight_cost=1000,
        discount_rate=0.07,
        lifetime=25,
        fom_cost=3,
    )
    study_dir = tmp_path / "study"
    PyPSAStudyConverter(n, study_dir, ".tsv", solver_name="coin").to_gems_study()
    system = yaml.safe_load((study_dir / "systems" / "input" / "system.yml").read_text())["system"]
    pinned = _params(system, "generator_pinned")
    assert (pinned["p_nom_min"], pinned["p_nom_max"]) == (70.0, 70.0)
    annuity = 0.07 / (1 - 1.07**-25)
    nyears = N_SNAPSHOTS / 8760
    assert _params(system, "generator_overnight")["capital_cost"] == pytest.approx(1000 * annuity * nyears + 3)


def test_storage_unit_charging_is_bounded_by_p_min_pu(converted: tuple[Path, dict[str, Any]]) -> None:
    """PyPSA bounds p_store by -p_min_pu * p_nom (it was bounded by p_max_pu * p_nom before)."""
    library = yaml.safe_load(LIBRARY.read_text())["library"]
    (storage_unit,) = [m for m in library["models"] if m["id"] == "storage_unit"]
    (p_store_upper,) = [c for c in storage_unit["constraints"] if c["id"] == "p_store_upper"]
    assert p_store_upper["expression"] == "p_store <= -p_min_pu * p_nom"
    _, system = converted
    assert _params(system, "storage_unit_phs")["p_min_pu"] == -0.5
