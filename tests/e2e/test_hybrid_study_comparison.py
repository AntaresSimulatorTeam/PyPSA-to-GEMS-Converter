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
Cross-checks PyPSA / GemsPy / Antares Modeler / Antares Solver on the same converted
GEMS study, using the antares-solver hybrid-study trick implemented in
`src/antares_hybrid_writer.py` (see that module's docstring for the full rationale:
why antares-modeler alone can't solve more than scenario 0, why a bare classic Antares
study with one inert virtual area unlocks antares-solver's Monte-Carlo-year loop, and
why optim-config.yml/system.yml/modeler-scenariobuilder.dat need patching).

`PyPSAStudyConverter.to_gems_study()` now builds this hybrid study automatically for
any multi-scenario, non-investment study (see `_has_extendable_capacity` there) --
`test_hybrid_two_scenarios_matches_pypsa_and_gemspy` below exercises exactly that
automatic path. The single-scenario baseline test builds it manually via
`AntaresHybridStudyWriter` directly, since the converter only triggers it for genuine
multi-scenario studies.
"""

import math
import shutil
import subprocess
from pathlib import Path

import pandas as pd
import pytest
from gems_craft.optim_config.parsing import OptimConfig, validate_optim_config
from gems_craft.study.folder import load_study
from gems_runner.simulation.optimization import build_problem
from gems_runner.simulation.time_block import TimeBlock
from pypsa import Network

from src.antares_hybrid_writer import AntaresHybridStudyWriter
from src.dependencies import get_antares_dir_name, get_antares_modeler_bin, get_antares_solver_bin
from src.pypsa_converter import PyPSAStudyConverter
from tests.utils import get_objective_value

PROJECT_ROOT = Path(__file__).resolve().parents[2]

HOURS_PER_WEEK = 168


def _antares_craft_available() -> bool:
    try:
        import antares.craft  # noqa: F401
    except ImportError:
        return False
    return True


@pytest.fixture(autouse=True)
def check_hybrid_prerequisites() -> None:
    """Skip (rather than fail) when the Antares binaries or antares-craft aren't available."""
    if not (PROJECT_ROOT / get_antares_dir_name()).is_dir():
        pytest.skip(
            f"Antares binaries not found. Please download version from "
            f"https://github.com/AntaresSimulatorTeam/Antares_Simulator/releases into {PROJECT_ROOT}"
        )
    if not _antares_craft_available():
        pytest.skip("antares-craft is not installed (needed to build the hybrid-study trick).")


def _run_hybrid_solver(antares_study_dir: Path) -> Path:
    """Step 6 of the trick (see src/antares_hybrid_writer.py's module docstring): run
    antares-solver with no mode flag (generaldata.ini defaults to Economy; there is
    nothing to expand) and return the resulting simulation_table CSV."""
    solver_bin = get_antares_solver_bin(PROJECT_ROOT)
    if antares_study_dir.joinpath("output").exists():
        shutil.rmtree(antares_study_dir / "output")

    result = subprocess.run(
        [str(solver_bin), "-i", str(antares_study_dir)],
        capture_output=True,
        text=True,
        check=False,
        cwd=str(solver_bin.parent),
    )
    print("================================")
    print("Antares solver (hybrid) output: returncode=%s" % result.returncode)
    print("stdout:", result.stdout)
    print("stderr:", result.stderr)
    print("================================")

    result_file = next(antares_study_dir.glob("output/**/simulation_table*"), None)
    if result_file is None:
        raise FileNotFoundError(
            f"No simulation_table found under {antares_study_dir / 'output'}; "
            "antares-solver likely failed to load the hybrid study, see stdout/stderr above."
        )
    return result_file


def _weighted_objective(simulation_table: Path, scenario_weights: list[float]) -> float:
    """`tests.utils.get_objective_value` only reads the *first* OBJECTIVE_VALUE row,
    which is fine for single-scenario runs but not for multi-scenario ones: antares-solver
    writes one OBJECTIVE_VALUE row per MC year/scenario_index, and they must be
    combined the same way PyPSA/GemsPy combine them (weighted average)."""
    df = pd.read_csv(simulation_table, usecols=["output", "scenario_index", "value"])
    per_scenario = df.query("output == 'OBJECTIVE_VALUE'").set_index("scenario_index")["value"]
    return float(sum(per_scenario[i] * scenario_weights[i] for i in range(len(scenario_weights))))


def _gemspy_objective(gems_systems_dir: Path, n_scenarios: int) -> float:
    gemspy_study = load_study(gems_systems_dir)
    optim_config = OptimConfig()
    optim_config.time_scope.first_time_step = 0
    optim_config.time_scope.last_time_step = HOURS_PER_WEEK - 1
    optim_config.scenario_scope.include = list(range(n_scenarios))
    validate_optim_config(optim_config, gemspy_study.system)

    timesteps = list(range(HOURS_PER_WEEK))
    block = TimeBlock(0, timesteps)
    scenario_ids = optim_config.scenario_scope.scenario_ids
    problem = build_problem(gemspy_study, block, scenario_ids, optim_config=optim_config)
    problem.solve(solver_name=optim_config.solver_options.name, **optim_config.solver_options.parsed_parameters())
    return float(problem.objective_value)


def _antares_modeler_objective(gems_systems_dir: Path) -> float:
    """Direct antares-modeler invocation, same pattern as end_2_end_tests.py's
    get_gems_study_objective(). Note: this only ever solves ONE scenario -- see
    module docstring -- so it is only compared for the deterministic fixture."""
    antares_modeler_bin = get_antares_modeler_bin(PROJECT_ROOT)
    if (gems_systems_dir / "output").exists():
        shutil.rmtree(gems_systems_dir / "output")

    subprocess.run(
        [str(antares_modeler_bin), str(gems_systems_dir)],
        capture_output=True,
        text=True,
        check=False,
        cwd=str(antares_modeler_bin.parent),
    )
    result_file = next(gems_systems_dir.glob("output/**/simulation_table*"), None)
    if result_file is None:
        raise FileNotFoundError(f"No simulation_table found under {gems_systems_dir / 'output'}")
    return get_objective_value(result_file)


def test_hybrid_deterministic_matches_all_execution_paths() -> None:
    """Single-scenario network: pypsa / antares-modeler / gemspy / antares-solver
    (hybrid trick) must all agree exactly.

    The converter itself only auto-builds the hybrid study for genuine multi-scenario
    studies (see `PyPSAStudyConverter.to_gems_study`), so this baseline calls
    `AntaresHybridStudyWriter` directly to confirm the trick introduces no bias before
    checking real multi-scenario behaviour below.
    """
    study_name = "test_hybrid_deterministic"
    gems_dir = PROJECT_ROOT / "tmp" / study_name

    network = Network(name="HybridDeterministic", snapshots=list(range(HOURS_PER_WEEK)))
    network.add("Bus", "town", v_nom=1)
    network.add("Load", "load1", bus="town", p_set=[80 + (i % 24) for i in range(HOURS_PER_WEEK)], q_set=0)
    network.add("Generator", "gen1", bus="town", p_nom_extendable=False, marginal_cost=50, p_nom=200)
    network.add("Generator", "gen2", bus="town", p_nom_extendable=False, marginal_cost=10, p_nom=50)

    PyPSAStudyConverter(pypsa_network=network, study_dir=gems_dir, series_file_format=".tsv").to_gems_study()
    gems_systems_dir = gems_dir / "systems"

    network.optimize()
    pypsa_objective = network.objective + network.objective_constant

    modeler_objective = _antares_modeler_objective(gems_systems_dir)
    gemspy_objective = _gemspy_objective(gems_systems_dir, n_scenarios=1)

    antares_study_dir = AntaresHybridStudyWriter(gems_dir).write(
        gems_systems_dir=gems_systems_dir, pypsa_network=network, n_scenarios=1
    )
    hybrid_result_file = _run_hybrid_solver(antares_study_dir)
    hybrid_objective = _weighted_objective(hybrid_result_file, scenario_weights=[1.0])

    print(
        f"pypsa={pypsa_objective} modeler={modeler_objective} "
        f"gemspy={gemspy_objective} antares_solver_hybrid={hybrid_objective}"
    )

    assert math.isclose(pypsa_objective, modeler_objective, rel_tol=1e-6)
    assert math.isclose(pypsa_objective, gemspy_objective, rel_tol=1e-6)
    assert math.isclose(pypsa_objective, hybrid_objective, rel_tol=1e-6)


def test_hybrid_two_scenarios_matches_pypsa_and_gemspy() -> None:
    """Two-scenario (stochastic) network: pypsa / gemspy / antares-solver (hybrid
    trick) must agree on the scenario-weighted objective.

    Unlike the deterministic baseline above, this study IS multi-scenario and
    non-investment, so `PyPSAStudyConverter.to_gems_study()` builds the hybrid study
    automatically -- this test uses that auto-built study directly (no manual
    `AntaresHybridStudyWriter` call), proving the production wiring itself works.

    antares-modeler alone is deliberately NOT asserted equal here: invoked directly
    (without the hybrid trick) it only ever solves one scenario -- this is exactly
    the multi-scenario gap the hybrid trick exists to validate (see
    src/antares_hybrid_writer.py's module docstring). It is still computed and
    printed for visibility.
    """
    study_name = "test_hybrid_two_scenarios"
    gems_dir = PROJECT_ROOT / "tmp" / study_name
    scenario_weights = [0.5, 0.5]

    network = Network(name="HybridTwoScenarios", snapshots=list(range(HOURS_PER_WEEK)))
    network.add("Bus", "town", v_nom=1)
    network.add("Load", "load1", bus="town", p_set=[80 + (i % 24) for i in range(HOURS_PER_WEEK)], q_set=0)
    network.add("Generator", "gen1", bus="town", p_nom_extendable=False, marginal_cost=50, p_nom=200)
    network.add(
        "Generator",
        "gen2",
        bus="town",
        p_nom_extendable=False,
        marginal_cost=10,
        p_nom=50,
        p_max_pu=[0.9] * HOURS_PER_WEEK,
    )
    network.set_scenarios({"low": 0.5, "high": 0.5})
    # Static (non-time-series) per-scenario overrides are collapsed to a single value
    # by the converter today (see gems_model_builder.py's first_per_component logic),
    # so the scenario axis must be exercised through a genuinely time-varying
    # attribute: gen2's availability differs per scenario.
    network.generators_t.p_max_pu[("low", "gen2")] = [1.0] * HOURS_PER_WEEK
    network.generators_t.p_max_pu[("high", "gen2")] = [0.2] * HOURS_PER_WEEK

    PyPSAStudyConverter(pypsa_network=network, study_dir=gems_dir, series_file_format=".tsv").to_gems_study()
    gems_systems_dir = gems_dir / "systems"

    network.optimize()
    pypsa_objective = network.objective + network.objective_constant

    modeler_objective = _antares_modeler_objective(gems_systems_dir)
    gemspy_objective = _gemspy_objective(gems_systems_dir, n_scenarios=2)

    antares_study_dir = gems_dir / AntaresHybridStudyWriter.STUDY_NAME
    assert antares_study_dir.is_dir(), (
        "to_gems_study() should have auto-built the antares-solver hybrid study "
        "for this multi-scenario, non-investment network by default"
    )
    hybrid_result_file = _run_hybrid_solver(antares_study_dir)
    hybrid_objective = _weighted_objective(hybrid_result_file, scenario_weights=scenario_weights)

    print(
        f"pypsa={pypsa_objective} modeler(single-scenario only)={modeler_objective} "
        f"gemspy={gemspy_objective} antares_solver_hybrid={hybrid_objective}"
    )

    assert math.isclose(pypsa_objective, gemspy_objective, rel_tol=1e-6)
    assert math.isclose(pypsa_objective, hybrid_objective, rel_tol=1e-6)


def test_unequal_scenario_weights_are_rejected() -> None:
    """Multi-scenario studies with UNEQUAL scenario weights (0.1 / 0.9) must be
    rejected at conversion time with a clear error, rather than silently producing a
    GEMS study whose GemsPy/antares-modeler results don't match PyPSA's.

    This is a real gap, not a hypothetical one: GemsPy's `expec()` operator (see the
    "Expectation semantics (average over scenarios) are applied automatically"
    UserWarning emitted during model resolution) currently computes a plain,
    UNWEIGHTED arithmetic mean across scenarios -- it does not consume PyPSA's
    scenario_weightings at all. With equal weights (0.5/0.5, see
    `test_hybrid_two_scenarios_matches_pypsa_and_gemspy` above) the unweighted and
    true weighted average coincide, so results are correct; with unequal weights
    (e.g. 0.1/0.9 here) they silently diverge:
      - pypsa's true weighted objective:  0.1 * 432600 + 0.9 * 701400 = 674520.0
      - gemspy's unweighted average:       (432600 + 701400) / 2      = 567000.0
    `PyPSAStudyConverter._validate_scenario_weightings` fails fast on construction
    instead, until GemsPy's `expec()` supports per-scenario weights.
    """
    study_name = "test_unequal_scenario_weights"
    gems_dir = PROJECT_ROOT / "tmp" / study_name

    network = Network(name="HybridUnequalWeights", snapshots=list(range(HOURS_PER_WEEK)))
    network.add("Bus", "town", v_nom=1)
    network.add("Load", "load1", bus="town", p_set=[80 + (i % 24) for i in range(HOURS_PER_WEEK)], q_set=0)
    network.add("Generator", "gen1", bus="town", p_nom_extendable=False, marginal_cost=50, p_nom=200)
    network.add(
        "Generator",
        "gen2",
        bus="town",
        p_nom_extendable=False,
        marginal_cost=10,
        p_nom=50,
        p_max_pu=[0.9] * HOURS_PER_WEEK,
    )
    network.set_scenarios({"low": 0.1, "high": 0.9})
    network.generators_t.p_max_pu[("low", "gen2")] = [1.0] * HOURS_PER_WEEK
    network.generators_t.p_max_pu[("high", "gen2")] = [0.2] * HOURS_PER_WEEK

    with pytest.raises(ValueError, match="same weight"):
        PyPSAStudyConverter(pypsa_network=network, study_dir=gems_dir, series_file_format=".tsv")
