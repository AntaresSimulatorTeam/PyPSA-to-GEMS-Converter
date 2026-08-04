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
Cross-checks PyPSA against the real Antares-Xpansion GEMS workflow (antares-problem-generator
+ benders, run via antares-xpansion-launcher) for multi-scenario INVESTMENT studies -- i.e.
studies with at least one extendable (p_nom_extendable=True) component, which
PyPSAStudyConverter._has_extendable_capacity routes through gems_study_writer's
optim-config.yml + prepare_xpansion_runnable_study (settings.ini / weights.txt), rather than
through the antares-solver hybrid trick used for non-investment studies (see
tests/e2e/test_hybrid_study_comparison.py).
"""

import logging
import time
from pathlib import Path

import pytest
from pypsa import Network

from src.dependencies import (
    get_antares_xpansion_dir_name,
    get_antares_xpansion_launcher_bin,
)
from src.pypsa_converter import PyPSAStudyConverter
from src.utils import read_xpansion_out_json, run_xpansion_launcher

current_dir = Path(__file__).resolve().parents[2]
logger = logging.getLogger(__name__)
logging.basicConfig(level=logging.INFO)


@pytest.fixture(scope="function", autouse=True)
def check_binaries() -> None:
    """Skip the test if the Antares Xpansion binaries are not present (the launcher bundles Antares)."""
    if not (current_dir / get_antares_xpansion_dir_name()).is_dir():
        pytest.skip(
            "Antares Xpansion binaries not found. "
            "Download from https://github.com/AntaresSimulatorTeam/antares-xpansion/releases"
        )


def _get_pypsa_total_objective(network: Network) -> float:
    objective = network.objective
    objective_constant = network.objective_constant
    assert objective is not None
    assert objective_constant is not None
    return objective + objective_constant


def _build_investment_test_network(scenario_weights: dict[str, float]) -> Network:
    """Tiny 2-bus expansion network with the given scenario probabilities (must sum to 1)."""
    if abs(sum(scenario_weights.values()) - 1.0) > 1e-9:
        raise ValueError(f"scenario_weights must sum to 1, got {sum(scenario_weights.values())}")

    network = Network(name="Simple_Network", snapshots=range(168))

    network.add("Carrier", "AC", co2_emissions=0)
    network.add("Bus", "bus 1", v_nom=220, carrier="AC")
    network.add("Bus", "bus 2", v_nom=220, carrier="AC")

    network.add("Load", "load1", bus="bus 1", p_set=[60 + (i % 24) for i in range(168)], q_set=0)
    network.add("Load", "load2", bus="bus 2", p_set=[40 + ((i + 6) % 24) for i in range(168)], q_set=0)

    base_p_max = [0.9 for _ in range(168)]

    network.add(
        "Generator",
        "gen1",
        bus="bus 1",
        p_nom_extendable=True,
        p_nom_min=140,
        marginal_cost=45,
        p_nom=140,
        p_max_pu=base_p_max,
        capital_cost=1000,
    )
    network.add(
        "Generator",
        "gen2",
        bus="bus 2",
        p_nom_extendable=True,
        p_nom_min=100,
        marginal_cost=55,
        p_nom=100,
        p_max_pu=base_p_max,
        capital_cost=900,
    )
    network.add(
        "Line",
        "line12",
        bus0="bus 1",
        bus1="bus 2",
        x=0.1,
        r=0.01,
        s_nom=200,
        s_nom_extendable=True,
        s_nom_min=200,
        capital_cost=100,
    )
    network.set_scenarios(scenario_weights)
    return network


@pytest.mark.parametrize(
    "scenario_weights",
    [
        {"low": 0.5, "high": 0.5},
        {"dry": 0.2, "normal": 0.5, "wet": 0.3},
        {"s1": 0.1, "s2": 0.2, "s3": 0.3, "s4": 0.4},
    ],
    ids=["two_scenarios", "three_scenarios", "four_scenarios"],
)
def test_2_stage_stochastic_investment_study_matches_pypsa(
    tmp_path: Path,
    scenario_weights: dict[str, float],
) -> None:
    """Convert an investment study with PyPSA scenario weights, run it through the real
    antares-xpansion-launcher, and check the Benders solution matches PyPSA's."""
    scenario_order = list(scenario_weights.keys())
    reference_scenario = scenario_order[0]
    study_name = f"test_2_stage_stochastic_{len(scenario_weights)}_scenarios"
    study_dir = tmp_path / study_name

    # Convert first: PyPSAStudyConverter deep-copies the network internally, so `network`
    # itself stays untouched here and can be optimized by PyPSA afterwards. Passing an
    # already-optimized network to the converter is not supported (it holds non-copyable
    # solver state), hence the ordering: convert, then optimize the same original object.
    network = _build_investment_test_network(scenario_weights)
    PyPSAStudyConverter(
        network,
        study_dir,
        ".tsv",
        solver_name="coin",
    ).to_gems_study()

    pypsa_start = time.perf_counter()
    status, condition = network.optimize(solver_name="cbc", include_objective_constant=True)
    pypsa_elapsed = time.perf_counter() - pypsa_start
    logger.info(
        "PyPSA stochastic solve (%s): status=%s condition=%s elapsed=%ss", study_name, status, condition, pypsa_elapsed
    )
    logger.info("PyPSA total objective: %s", _get_pypsa_total_objective(network))
    assert status == "ok"
    assert condition == "optimal"

    study_root = study_dir / "systems"
    assert (study_root / "input" / "optim-config.yml").exists()
    settings_ini = study_root / "user" / "expansion" / "settings.ini"
    assert settings_ini.exists()
    settings_text = settings_ini.read_text(encoding="utf-8")
    assert "yearly-weights" in settings_text
    weights_file = study_root / "user" / "expansion" / "weights" / "weights.txt"
    assert weights_file.exists()
    weights = [float(x) for x in weights_file.read_text(encoding="utf-8").splitlines() if x.strip()]
    assert len(weights) == len(scenario_weights)
    assert sum(weights) == pytest.approx(1.0)

    launcher_bin = get_antares_xpansion_launcher_bin(current_dir)
    xpansion_start = time.perf_counter()
    result = run_xpansion_launcher(study_root, launcher_bin, logger=logger)
    xpansion_elapsed = time.perf_counter() - xpansion_start
    logger.info(
        "Antares-Xpansion launcher (%s): returncode=%s elapsed=%ss", study_name, result.returncode, xpansion_elapsed
    )
    assert result.returncode == 0, (
        f"antares-xpansion-launcher failed (returncode={result.returncode}).\n"
        f"--- stdout (tail) ---\n{result.stdout[-6000:]}\n"
        f"--- stderr (tail) ---\n{result.stderr[-4000:]}"
    )

    # Read the optimum directly from the Antares-Xpansion out.json and compare with PyPSA.
    xpansion_solution = read_xpansion_out_json(study_root)["solution"]
    assert xpansion_solution["problem_status"] == "OPTIMAL"
    xpansion_values = xpansion_solution["values"]

    pypsa_objective = _get_pypsa_total_objective(network)
    pypsa_gen1 = float(network.generators.loc[(reference_scenario, "gen1"), "p_nom_opt"])
    pypsa_gen2 = float(network.generators.loc[(reference_scenario, "gen2"), "p_nom_opt"])
    logger.info(
        "PyPSA vs Antares-Xpansion (%s): objective %s / %s | gen1.p_nom %s / %s | gen2.p_nom %s / %s",
        study_name,
        pypsa_objective,
        xpansion_solution["overall_cost"],
        pypsa_gen1,
        xpansion_values["generator_gen1.p_nom"],
        pypsa_gen2,
        xpansion_values["generator_gen2.p_nom"],
    )

    assert xpansion_solution["overall_cost"] == pytest.approx(pypsa_objective)
    assert xpansion_values["generator_gen1.p_nom"] == pytest.approx(pypsa_gen1)
    assert xpansion_values["generator_gen2.p_nom"] == pytest.approx(pypsa_gen2)
