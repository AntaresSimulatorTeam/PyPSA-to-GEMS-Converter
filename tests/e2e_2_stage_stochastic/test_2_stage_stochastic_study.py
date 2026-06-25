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
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pytest
from pypsa import Network

from src.dependencies import (
    get_antares_dir_name,
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
    """Skip the test if Antares Simulator or Antares Xpansion binaries are not present."""
    if not (current_dir / get_antares_dir_name()).is_dir():
        pytest.skip(
            "Antares Simulator binaries not found. "
            "Download from https://github.com/AntaresSimulatorTeam/Antares_Simulator/releases"
        )
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


def _build_stochastic_test_network(scenario_weights: dict[str, float]) -> Network:
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


def _run_pypsa_xpansion_e2e(
    tmp_path: Path,
    scenario_weights: dict[str, float],
    study_name: str,
    *,
    check_objective_against_pypsa: bool = True,
) -> Network:
    """Solve with PyPSA, convert, run the Antares-Xpansion launcher; assert objectives and investments match."""
    scenario_order = list(scenario_weights.keys())
    reference_scenario = scenario_order[0]

    pypsa_network = _build_stochastic_test_network(scenario_weights)
    pypsa_start = time.perf_counter()
    status, condition = pypsa_network.optimize(solver_name="cbc", include_objective_constant=True)
    pypsa_elapsed = time.perf_counter() - pypsa_start
    logger.info(
        "PyPSA stochastic solve (%s): status=%s condition=%s elapsed=%ss", study_name, status, condition, pypsa_elapsed
    )
    logger.info("PyPSA total objective: %s", _get_pypsa_total_objective(pypsa_network))
    assert status == "ok"
    assert condition == "optimal"

    network = _build_stochastic_test_network(scenario_weights)
    study_dir = tmp_path / study_name
    PyPSAStudyConverter(
        network,
        study_dir,
        ".tsv",
        solver_name="coin",
    ).to_gems_study()

    study_root = study_dir / "systems"
    assert (study_root / "input" / "optim-config.yml").exists()
    settings_ini = study_root / "user" / "expansion" / "settings.ini"
    assert settings_ini.exists()
    # settings.ini is always empty: Antares-Xpansion weights the Monte-Carlo years (scenarios) equally.
    assert settings_ini.read_text(encoding="utf-8").strip() == ""
    assert not (study_root / "user" / "expansion" / "weights").exists()

    launcher_bin = get_antares_xpansion_launcher_bin(current_dir)
    result = run_xpansion_launcher(study_root, launcher_bin, logger=logger)
    assert result.returncode == 0, f"antares-xpansion-launcher failed:\n{result.stderr[-4000:]}"

    xpansion_result = read_xpansion_out_json(study_root)
    xpansion_solution = xpansion_result["solution"]
    assert xpansion_solution["problem_status"] == "OPTIMAL"

    comparison = _compare_pypsa_vs_xpansion(pypsa_network, xpansion_solution, reference_scenario)
    _log_comparison_table(study_name, comparison)

    # Investment decisions must match between PyPSA and Antares-Xpansion in every case.
    for row in comparison:
        if row.name == "total objective" and not check_objective_against_pypsa:
            continue
        assert row.xpansion == pytest.approx(row.pypsa, rel=1e-4, abs=1e-3), (
            f"{study_name}: {row.name} differs (pypsa={row.pypsa}, xpansion={row.xpansion})"
        )
    return pypsa_network


@dataclass
class _ComparisonRow:
    name: str
    pypsa: float
    xpansion: float

    @property
    def abs_diff(self) -> float:
        return abs(self.pypsa - self.xpansion)

    @property
    def matches(self) -> bool:
        return self.abs_diff <= 1e-3 + 1e-4 * abs(self.pypsa)


def _compare_pypsa_vs_xpansion(
    pypsa_network: Network,
    xpansion_solution: dict[str, Any],
    reference_scenario: str,
) -> list[_ComparisonRow]:
    """Build a PyPSA vs Antares-Xpansion comparison for the objective and the investment decisions."""
    xpansion_values = xpansion_solution["values"]
    rows = [
        _ComparisonRow(
            "total objective",
            _get_pypsa_total_objective(pypsa_network),
            float(xpansion_solution["overall_cost"]),
        ),
        _ComparisonRow(
            "gen1.p_nom",
            float(pypsa_network.generators.loc[(reference_scenario, "gen1"), "p_nom_opt"]),
            float(xpansion_values["generator_gen1.p_nom"]),
        ),
        _ComparisonRow(
            "gen2.p_nom",
            float(pypsa_network.generators.loc[(reference_scenario, "gen2"), "p_nom_opt"]),
            float(xpansion_values["generator_gen2.p_nom"]),
        ),
    ]
    return rows


def _log_comparison_table(study_name: str, comparison: list[_ComparisonRow]) -> None:
    """Log a readable PyPSA vs Antares-Xpansion side-by-side comparison table."""
    header = f"{'quantity':<18}{'pypsa':>18}{'xpansion':>18}{'abs_diff':>14}{'match':>8}"
    lines = [f"PyPSA vs Antares-Xpansion comparison ({study_name})", header, "-" * len(header)]
    for row in comparison:
        lines.append(
            f"{row.name:<18}{row.pypsa:>18.6f}{row.xpansion:>18.6f}{row.abs_diff:>14.6f}"
            f"{('yes' if row.matches else 'NO'):>8}"
        )
    logger.info("\n".join(lines))


def test_2_stage_stochastic_study_two_scenarios(tmp_path: Path) -> None:
    _run_pypsa_xpansion_e2e(
        tmp_path,
        {"low": 0.5, "high": 0.5},
        "test_2_stage_stochastic_study_two_scenarios",
    )


@pytest.mark.parametrize(
    "scenario_weights",
    [
        {"dry": 0.2, "normal": 0.5, "wet": 0.3},
        {"s1": 0.1, "s2": 0.2, "s3": 0.3, "s4": 0.4},
    ],
    ids=["three_scenarios", "four_scenarios"],
)
def test_2_stage_stochastic_study_weighted_scenarios(
    tmp_path: Path,
    scenario_weights: dict[str, float],
) -> None:
    """PyPSA vs Benders with 3 or 4 scenarios whose weights sum to 1."""
    study_name = f"test_2_stage_stochastic_{len(scenario_weights)}_scenarios"
    _run_pypsa_xpansion_e2e(
        tmp_path,
        scenario_weights,
        study_name,
        check_objective_against_pypsa=False,
    )
