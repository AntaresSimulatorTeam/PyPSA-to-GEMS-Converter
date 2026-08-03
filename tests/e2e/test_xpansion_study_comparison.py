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

"""
Cross-checks PyPSA / antares-xpansion-launcher on the same converted, multi-scenario
INVESTMENT (extendable-capacity) GEMS study.

Why this exists
----------------
`tests/e2e/test_hybrid_study_comparison.py` validates the antares-solver hybrid trick for
multi-scenario, non-investment (dispatch-only) studies. Investment studies (some component has
`p_nom_extendable`/`e_nom_extendable` = True) have a different real-Antares-engine gap:
antares-modeler invoked directly only ever solves scenario 0, and the classic-study
`candidates.ini` Xpansion workflow has no notion of a GEMS `system.yml` at all. Instead,
`antares-xpansion-launcher`'s own "gems" driver step loads the GEMS system natively -- but it
still requires the classic-study `study.antares` marker (confirmed empirically: pointing it at a
bare `systems/` folder fails with "does not seem to be a valid study"), so it is run against the
SAME companion classic Antares study `AntaresHybridStudyWriter` builds for the non-investment
case (see that module's docstring), with a `user/expansion/settings.ini` added on top by
`GemsStudyWriter.prepare_xpansion_runnable_study`. `PyPSAStudyConverter.to_gems_study()` wires
this automatically whenever a multi-scenario study has extendable capacity (see
`_has_extendable_capacity`) -- both tests below exercise that automatic path directly.
"""

import math
import shutil
from pathlib import Path

import pytest
from pypsa import Network

from src.antares_hybrid_writer import AntaresHybridStudyWriter
from src.dependencies import get_antares_dir_name, get_antares_xpansion_launcher_bin
from src.pypsa_converter import PyPSAStudyConverter
from src.utils import read_xpansion_out_json, run_xpansion_launcher

PROJECT_ROOT = Path(__file__).resolve().parents[2]

HOURS_PER_WEEK = 168


@pytest.fixture(autouse=True)
def check_xpansion_prerequisites() -> None:
    """Skip (rather than fail) when the Antares/Antares-Xpansion binaries aren't available."""
    if not (PROJECT_ROOT / get_antares_dir_name()).is_dir():
        pytest.skip(
            f"Antares binaries not found. Please download version from "
            f"https://github.com/AntaresSimulatorTeam/Antares_Simulator/releases into {PROJECT_ROOT}"
        )
    if not get_antares_xpansion_launcher_bin(PROJECT_ROOT).exists():
        pytest.skip(
            "antares-xpansion-launcher not found. Please download it from "
            f"https://github.com/AntaresSimulatorTeam/antares-xpansion/releases into {PROJECT_ROOT}"
        )


def _pypsa_total_objective(network: Network) -> float:
    return float(network.objective + network.objective_constant)


def test_xpansion_two_scenarios_matches_pypsa() -> None:
    """Two-bus, two-scenario network with two extendable generators and an extendable line:
    pypsa vs antares-xpansion-launcher must agree on the objective and on every candidate's
    invested capacity."""
    study_name = "test_xpansion_two_scenarios"
    gems_dir = PROJECT_ROOT / "tmp" / study_name
    scenario_weights = {"low": 0.5, "high": 0.5}

    network = Network(name="XpansionTwoScenarios", snapshots=list(range(HOURS_PER_WEEK)))
    network.add("Carrier", "AC", co2_emissions=0)
    network.add("Bus", "bus1", v_nom=220, carrier="AC")
    network.add("Bus", "bus2", v_nom=220, carrier="AC")
    network.add("Load", "load1", bus="bus1", p_set=[60 + (i % 24) for i in range(HOURS_PER_WEEK)], q_set=0)
    network.add("Load", "load2", bus="bus2", p_set=[40 + ((i + 6) % 24) for i in range(HOURS_PER_WEEK)], q_set=0)
    network.add(
        "Generator",
        "gen1",
        bus="bus1",
        p_nom_extendable=True,
        p_nom_min=140,
        marginal_cost=45,
        p_nom=140,
        capital_cost=1000,
        p_max_pu=[0.9] * HOURS_PER_WEEK,
    )
    network.add(
        "Generator",
        "gen2",
        bus="bus2",
        p_nom_extendable=True,
        p_nom_min=100,
        marginal_cost=55,
        p_nom=100,
        capital_cost=900,
        p_max_pu=[0.9] * HOURS_PER_WEEK,
    )
    network.add(
        "Line",
        "line12",
        bus0="bus1",
        bus1="bus2",
        x=0.1,
        r=0.01,
        s_nom=200,
        s_nom_extendable=True,
        s_nom_min=200,
        capital_cost=100,
    )
    network.set_scenarios(scenario_weights)

    PyPSAStudyConverter(pypsa_network=network, study_dir=gems_dir, series_file_format=".tsv", solver_name="coin").to_gems_study()

    network.optimize(solver_name="cbc", include_objective_constant=True)
    pypsa_objective = _pypsa_total_objective(network)

    antares_hybrid_dir = gems_dir / AntaresHybridStudyWriter.STUDY_NAME
    assert antares_hybrid_dir.is_dir(), (
        "to_gems_study() should have auto-built the antares-hybrid-master companion study for "
        "this multi-scenario investment network by default"
    )
    settings_ini = antares_hybrid_dir / "user" / "expansion" / "settings.ini"
    assert settings_ini.exists()
    assert "solver = Cbc" in settings_ini.read_text(encoding="utf-8")

    launcher_bin = get_antares_xpansion_launcher_bin(PROJECT_ROOT)
    result = run_xpansion_launcher(antares_hybrid_dir, launcher_bin)
    assert result.returncode == 0, (
        f"antares-xpansion-launcher failed (returncode={result.returncode}).\n"
        f"--- stdout (tail) ---\n{result.stdout[-6000:]}\n"
        f"--- stderr (tail) ---\n{result.stderr[-4000:]}"
    )

    solution = read_xpansion_out_json(antares_hybrid_dir)["solution"]
    assert solution["problem_status"] == "OPTIMAL"

    print(
        f"pypsa={pypsa_objective} antares_xpansion_launcher={solution['overall_cost']} "
        f"gen1.p_nom={network.generators.loc[('low', 'gen1'), 'p_nom_opt']}/"
        f"{solution['values']['generator_gen1.p_nom']} "
        f"gen2.p_nom={network.generators.loc[('low', 'gen2'), 'p_nom_opt']}/"
        f"{solution['values']['generator_gen2.p_nom']}"
    )

    assert math.isclose(pypsa_objective, solution["overall_cost"], rel_tol=1e-6)
    assert math.isclose(
        float(network.generators.loc[("low", "gen1"), "p_nom_opt"]),
        solution["values"]["generator_gen1.p_nom"],
        rel_tol=1e-6,
    )
    assert math.isclose(
        float(network.generators.loc[("low", "gen2"), "p_nom_opt"]),
        solution["values"]["generator_gen2.p_nom"],
        rel_tol=1e-6,
    )

    shutil.rmtree(gems_dir, ignore_errors=True)


def test_xpansion_two_scenarios_storage_matches_pypsa() -> None:
    """Two-bus, two-scenario network with an extendable storage unit (instead of the
    generator+line candidates above): pypsa vs antares-xpansion-launcher must still agree.

    Uses two buses linked by a (non-extendable) line rather than a single bus: a single-bus
    network with no Line has no genuine use for the bus's voltage-angle ("theta") variable, and
    antares-problem-generator (v1.9.0) still emits an MPS bound row for it without ever declaring
    the corresponding column, crashing the Benders solve with "invalid status 168" -- a bundled-
    binary limitation unrelated to this converter, worked around here by always giving the
    network at least one Line.

    Every component whose model declares a "master-and-subproblems" decomposition variable
    (generators/links/storage_units/stores' p_nom or e_nom, see resources/optim-config.yml) is
    kept extendable here for the same reason: a NON-extendable one (a fixed, not a free,
    master variable) hits a related antares-problem-generator MPS-writing bug (a bound row in
    master.mps referencing a column that never got declared), independently of this converter.
    """
    study_name = "test_xpansion_two_scenarios_storage"
    gems_dir = PROJECT_ROOT / "tmp" / study_name
    scenario_weights = {"low": 0.5, "high": 0.5}

    network = Network(name="XpansionStorageTwoScenarios", snapshots=list(range(HOURS_PER_WEEK)))
    network.add("Bus", "bus1", v_nom=220)
    network.add("Bus", "bus2", v_nom=220)
    network.add("Load", "load1", bus="bus1", p_set=[80 + (i % 24) for i in range(HOURS_PER_WEEK)], q_set=0)
    network.add(
        "Generator",
        "gen1",
        bus="bus1",
        p_nom_extendable=True,
        p_nom_min=200,
        marginal_cost=50,
        p_nom=200,
        capital_cost=50,
    )
    network.add("Line", "line12", bus0="bus1", bus1="bus2", x=0.1, r=0.01, s_nom=200, s_nom_extendable=False)
    network.add(
        "StorageUnit",
        "storage1",
        bus="bus2",
        p_nom_extendable=True,
        p_nom_min=20,
        capital_cost=200,
        marginal_cost=5,
        max_hours=4,
        efficiency_store=0.95,
        efficiency_dispatch=0.95,
        cyclic_state_of_charge=True,
    )
    network.set_scenarios(scenario_weights)
    # Non-time-series scenario overrides collapse to a single value (see
    # test_hybrid_study_comparison.py's comment on the same converter limitation), so the
    # scenario axis is exercised through a genuinely time-varying attribute.
    network.generators_t.p_max_pu[("low", "gen1")] = [1.0] * HOURS_PER_WEEK
    network.generators_t.p_max_pu[("high", "gen1")] = [0.6] * HOURS_PER_WEEK

    PyPSAStudyConverter(pypsa_network=network, study_dir=gems_dir, series_file_format=".tsv", solver_name="coin").to_gems_study()

    network.optimize(solver_name="cbc", include_objective_constant=True)
    pypsa_objective = _pypsa_total_objective(network)

    antares_hybrid_dir = gems_dir / AntaresHybridStudyWriter.STUDY_NAME
    assert antares_hybrid_dir.is_dir()

    launcher_bin = get_antares_xpansion_launcher_bin(PROJECT_ROOT)
    result = run_xpansion_launcher(antares_hybrid_dir, launcher_bin)
    assert result.returncode == 0, (
        f"antares-xpansion-launcher failed (returncode={result.returncode}).\n"
        f"--- stdout (tail) ---\n{result.stdout[-6000:]}\n"
        f"--- stderr (tail) ---\n{result.stderr[-4000:]}"
    )

    solution = read_xpansion_out_json(antares_hybrid_dir)["solution"]
    assert solution["problem_status"] == "OPTIMAL"

    assert math.isclose(pypsa_objective, solution["overall_cost"], rel_tol=1e-6)
    assert math.isclose(
        float(network.storage_units.loc[("low", "storage1"), "p_nom_opt"]),
        solution["values"]["storage_unit_storage1.p_nom"],
        rel_tol=1e-6,
    )

    shutil.rmtree(gems_dir, ignore_errors=True)
