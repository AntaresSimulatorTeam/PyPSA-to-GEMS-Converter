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
from __future__ import annotations

import json
import logging
import re
import shutil
import subprocess
from collections.abc import Sequence
from pathlib import Path
from typing import Any, cast

import highspy
import pandas as pd
import polars as pl
from pypsa import Network

PYPSA_CONVERTER_MAX_FLOAT = 100_000_000_000


def any_to_float(el: Any) -> float:
    """Auxiliary function for type consistency"""
    try:
        return max(min(float(el), PYPSA_CONVERTER_MAX_FLOAT), PYPSA_CONVERTER_MAX_FLOAT * -1)
    except (TypeError, ValueError):
        raise TypeError(f"Could not convert {el} to float")


def check_time_series_format(series_file_format: str) -> str:
    if series_file_format not in {".csv", ".tsv", "csv", "tsv"}:
        raise ValueError(f"Invalid series file format: {series_file_format}")

    if series_file_format in {"csv", "tsv"}:
        return "." + series_file_format

    return series_file_format


def determine_pypsa_study_type(pypsa_network: Network) -> tuple[Network, dict[str, float]]:
    """Determine study type; studies without scenarios get one default scenario so we always convert as WITH_SCENARIOS."""

    if hasattr(pypsa_network, "has_scenarios") and pypsa_network.has_scenarios:
        return pypsa_network, cast(dict[str, float], pypsa_network.scenario_weightings["weight"].to_dict())

    # No scenarios: add single default scenario so all studies use the same multi-index path
    pypsa_network.set_scenarios({"default": 1})
    return pypsa_network, cast(dict[str, float], pypsa_network.scenario_weightings["weight"].to_dict())


_SLAVE_MPS_NAME_RE = re.compile(r"^(?:problem-)?\d+-\d+(?:--optim-nb-\d+)?\.mps$")
_PROBLEM_YEAR_RE = re.compile(r"^(?:problem-)?(\d+)-\d+(?:--optim-nb-\d+)?$")

_XPANSION_SOLVER_TO_BENDERS = {"cbc": "COIN", "coin": "COIN", "xpress": "XPRESS"}


def _parse_xpansion_settings_ini(study_root: Path) -> dict[str, str]:
    settings_path = study_root / "user" / "expansion" / "settings.ini"
    if not settings_path.is_file():
        return {}
    options: dict[str, str] = {}
    for line in settings_path.read_text(encoding="utf-8").splitlines():
        stripped = line.strip()
        if not stripped or "=" not in stripped:
            continue
        key, _, value = stripped.partition("=")
        options[key.strip()] = value.strip()
    return options


def _read_yearly_weights(study_root: Path) -> list[float]:
    weights_path = study_root / "user" / "expansion" / "weights" / "weights.txt"
    if not weights_path.is_file():
        return []
    return [float(line) for line in weights_path.read_text(encoding="utf-8").splitlines() if line.strip()]


def _latest_simulation_dir(study_root: Path) -> Path:
    output_dir = study_root / "output"
    sim_dirs = [path for path in output_dir.iterdir() if path.is_dir()]
    if not sim_dirs:
        raise FileNotFoundError(f"No simulation directory under {output_dir}")
    return max(sim_dirs, key=lambda path: path.stat().st_mtime)


def _problem_names_from_structure(structure_path: Path) -> list[str]:
    names: list[str] = []
    seen: set[str] = set()
    for line in structure_path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        name = line.split()[0]
        if _PROBLEM_YEAR_RE.match(name) and name not in seen:
            seen.add(name)
            names.append(name)
    return names


def _mc_year_from_problem_name(problem_name: str) -> int:
    match = _PROBLEM_YEAR_RE.match(problem_name)
    if match is None:
        raise ValueError(f"Cannot parse Monte-Carlo year from subproblem name {problem_name!r}")
    return int(match.group(1))


def write_benders_lp_weights(lp_dir: Path, yearly_weights: Sequence[float]) -> Path:
    """Write the Benders ``weights.txt`` that ``YearlyWeightsWriter`` would have produced.

    The GEMS launcher path (antares-problem-generator + benders) never copies
    ``user/expansion/weights/weights.txt`` into ``lp/``. Benders then fails with
    ``Cannot open file ./weights.txt`` / ``WEIGHT_SUM not found``. Each slave MPS of
    Monte-Carlo year *i* gets that year's weight; ``WEIGHT_SUM`` is the sum of the
    yearly weights (not of the per-MPS rows), matching lp_namer.
    """
    structure_path = lp_dir / "structure.txt"
    problem_names = _problem_names_from_structure(structure_path)
    if not problem_names:
        raise FileNotFoundError(f"No subproblem names in {structure_path}")

    lines: list[str] = []
    for name in problem_names:
        year = _mc_year_from_problem_name(name)
        if year < 1 or year > len(yearly_weights):
            raise ValueError(
                f"Subproblem {name} maps to MC year {year}, but yearly-weights has {len(yearly_weights)} entries"
            )
        lines.append(f"{name} {yearly_weights[year - 1]}")
    lines.append(f"WEIGHT_SUM {sum(yearly_weights)}")
    weights_path = lp_dir / "weights.txt"
    weights_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return weights_path


def _write_benders_options(
    lp_dir: Path,
    simulation_dir: Path,
    *,
    solver_name: str,
    master_formulation: str,
    absolute_gap: float,
    relative_gap: float,
    slave_weight: str,
    slave_weight_value: float,
) -> Path:
    expansion_dir = simulation_dir / "expansion"
    expansion_dir.mkdir(exist_ok=True)
    options_path = lp_dir / "options.json"
    options = {
        "MAX_ITERATIONS": -1,
        "ABSOLUTE_GAP": absolute_gap,
        "RELATIVE_GAP": relative_gap,
        "RELAXED_GAP": 1e-05,
        "AGGREGATION": False,
        "OUTPUTROOT": ".",
        "TRACE": True,
        "SLAVE_WEIGHT": slave_weight,
        "SLAVE_WEIGHT_VALUE": slave_weight_value,
        "MASTER_NAME": "master",
        "PROBLEMS_FORMAT": "mps",
        "STRUCTURE_FILE": "structure.txt",
        "INPUTROOT": ".",
        "CSV_NAME": "benders_output_trace",
        "BOUND_ALPHA": False,
        "SEPARATION_PARAM": 0.5,
        "BATCH_SIZE": 0,
        "CACHE_PROBLEMS": False,
        "MASTER_SOLUTION_TOLERANCE": 0.0001,
        "CUT_COEFFICIENT_TOLERANCE": 0.005,
        "KEEP_FULL": False,
        "FULL_DIR": "full",
        "JSON_FILE": str(expansion_dir / "out.json"),
        "LAST_ITERATION_JSON_FILE": str(expansion_dir / "last_iteration.json"),
        "MASTER_FORMULATION": master_formulation,
        "SOLVER_NAME": solver_name,
        "TIME_LIMIT": 1e12,
        "LOG_LEVEL": 0,
        "LAST_MASTER_MPS": "master_last_iteration",
        "LAST_MASTER_BASIS": "master_last_basis.bss",
        "DO_OUTER_LOOP": False,
        "OUTER_LOOP_OPTION_FILE": "adequacy_criterion.yml",
        "AREA_FILE": "area.txt",
    }
    options_path.write_text(json.dumps(options, indent=4), encoding="utf-8")
    return options_path


def run_xpansion_launcher(
    study_root: Path,
    launcher_bin: Path,
    *,
    extra_args: Sequence[str] | None = None,
    logger: logging.Logger | None = None,
) -> subprocess.CompletedProcess[str]:
    """Run the GEMS Xpansion workflow on a converter-generated hybrid study.

    The official ``antares-xpansion-launcher`` GEMS step (``gems_driver``) runs
    ``antares-problem-generator`` then benders, but unlike lp_namer it never copies
    ``yearly-weights`` into ``lp/weights.txt`` in the Benders ``WEIGHT_SUM`` format.
    We therefore drive the same two binaries ourselves and write that file between them.
    ``launcher_bin`` still locates the Xpansion install (``bin/`` next to the launcher).
    """
    if extra_args and logger is not None:
        logger.info("Xpansion extra args (GEMS runner keeps MPS): %s", " ".join(str(a) for a in extra_args))
    install_bin = launcher_bin.parent / "bin"
    problem_generator = install_bin / "antares-problem-generator"
    benders = install_bin / "benders"

    locker = study_root / ".xpansion_locker"
    if locker.exists():
        locker.unlink()

    if logger is not None:
        logger.info("Running antares-problem-generator on %s", study_root)
    generated = subprocess.run(
        [str(problem_generator), str(study_root.resolve())],
        capture_output=True,
        text=True,
        check=False,
        cwd=str(install_bin),
    )
    if generated.returncode != 0:
        if logger is not None:
            logger.error("antares-problem-generator failed (returncode=%s)", generated.returncode)
        return generated

    simulation_dir = _latest_simulation_dir(study_root)
    lp_dir = simulation_dir / "lp"
    if lp_dir.exists():
        shutil.rmtree(lp_dir)
    lp_dir.mkdir()
    for src in simulation_dir.iterdir():
        if src.suffix == ".mps" or src.name == "structure.txt":
            shutil.move(str(src), lp_dir / src.name)
    (lp_dir / "area.txt").touch()

    settings = _parse_xpansion_settings_ini(study_root)
    yearly_weights = _read_yearly_weights(study_root)
    if yearly_weights:
        write_benders_lp_weights(lp_dir, yearly_weights)
        slave_weight = "weights.txt"
        slave_weight_value = float(len(yearly_weights))
    else:
        slave_weight = "CONSTANT"
        n_subproblems = sum(1 for path in lp_dir.glob("*.mps") if _SLAVE_MPS_NAME_RE.match(path.name))
        slave_weight_value = float(n_subproblems) if n_subproblems else 1.0

    solver_key = settings.get("solver", "Cbc").lower()
    solver_name = _XPANSION_SOLVER_TO_BENDERS.get(solver_key, "COIN")
    _write_benders_options(
        lp_dir,
        simulation_dir,
        solver_name=solver_name,
        master_formulation=settings.get("master", "integer"),
        absolute_gap=float(settings.get("optimality_gap", "0")),
        relative_gap=float(settings.get("relative_gap", "1e-6")),
        slave_weight=slave_weight,
        slave_weight_value=slave_weight_value,
    )

    if logger is not None:
        logger.info("Running benders in %s", lp_dir)
    solved = subprocess.run(
        [str(benders), "options.json"],
        capture_output=True,
        text=True,
        check=False,
        cwd=str(lp_dir),
    )
    solved.stdout = (generated.stdout or "") + (solved.stdout or "")
    solved.stderr = (generated.stderr or "") + (solved.stderr or "")
    return solved


def read_xpansion_out_json(study_root: Path) -> dict[str, Any]:
    """Load the Antares-Xpansion ``out.json`` produced by the launcher under ``<study_root>/output``."""
    output_root = study_root / "output"
    out_json = next(output_root.glob("**/out.json"), None)
    if out_json is None:
        raise FileNotFoundError(f"No Antares-Xpansion out.json found under {output_root}")
    return cast(dict[str, Any], json.loads(out_json.read_text(encoding="utf-8")))


def read_mps_problem_size(mps_path: Path) -> tuple[int, int]:
    """Return ``(n_variables, n_constraints)`` by loading the MPS with HiGHS."""
    highs = highspy.Highs()
    highs.setOptionValue("output_flag", False)
    status = highs.readModel(str(mps_path))
    if status not in (highspy.HighsStatus.kOk, highspy.HighsStatus.kWarning):
        raise ValueError(f"HiGHS failed to read {mps_path}: {status}")
    return int(highs.getNumCol()), int(highs.getNumRow())


def read_xpansion_mps_sizes(study_root: Path) -> dict[str, int]:
    """
    Collect master + one-slave MPS sizes under ``<study_root>/output/**/lp``.

    Requires the launcher to have been run with ``--keepMps``. Master investment
    variables are duplicated in every slave MPS, so the merged variable count is
    ``master_vars + n_subproblems * (slave_vars - master_vars)``.
    """
    lp_dirs = sorted((study_root / "output").glob("**/lp"))
    if not lp_dirs:
        raise FileNotFoundError(f"No Xpansion lp/ directory under {study_root / 'output'}")
    lp_dir = lp_dirs[-1]

    master_mps = lp_dir / "master.mps"
    if not master_mps.is_file():
        raise FileNotFoundError(f"No master.mps in {lp_dir} (run launcher with --keepMps)")

    master_vars, master_cons = read_mps_problem_size(master_mps)
    slave_mps = sorted(path for path in lp_dir.glob("*.mps") if _SLAVE_MPS_NAME_RE.match(path.name))
    n_subproblems = len(slave_mps)
    if n_subproblems == 0:
        raise FileNotFoundError(f"No slave MPS files in {lp_dir}")

    slave_vars, slave_cons = read_mps_problem_size(slave_mps[0])
    # Linking (master) vars appear once in master.mps and again in every slave.
    merged_vars = master_vars + n_subproblems * (slave_vars - master_vars)
    merged_cons = master_cons + n_subproblems * slave_cons

    return {
        "number_of_xpansion_subproblems": n_subproblems,
        "number_of_variables_xpansion_master": master_vars,
        "number_of_constraints_xpansion_master": master_cons,
        "number_of_variables_xpansion_subproblem": slave_vars,
        "number_of_constraints_xpansion_subproblem": slave_cons,
        "number_of_variables_xpansion": merged_vars,
        "number_of_constraints_xpansion": merged_cons,
    }


# --- PyPSA pandas to Polars conversion (PyPSA objects stay as pandas) ---


def _flatten_multiindex_columns(cols: pd.MultiIndex, sep: str = "__") -> list[str]:
    """Convert MultiIndex columns to flat names: (scenario, component) -> 'scenario__component'."""
    return [sep.join(str(c) for c in level_vals) for level_vals in cols]


def _make_columns_unique(df: pd.DataFrame) -> pd.DataFrame:
    """Ensure column names are unique; Polars requires unique string names."""
    cols = df.columns.astype(str)
    if len(cols) == len(set(cols)):
        return df
    seen: dict[str, int] = {}
    new_names: list[str] = []
    for c in cols:
        count = seen.get(c, 0)
        seen[c] = count + 1
        new_names.append(f"{c}_{count}" if count else c)
    return df.set_axis(new_names, axis="columns")


def static_pypsa_to_polars(static_df: pd.DataFrame) -> pl.DataFrame:
    """
    Convert PyPSA static DataFrame (MultiIndex index = (scenario, component), columns = params)
    to Polars with columns [scenario, component, ...param_names].
    """
    if static_df.empty:
        return pl.DataFrame()
    df = static_df.reset_index()
    # Normalize first two columns to scenario, component for internal use
    rename = {df.columns[0]: "scenario", df.columns[1]: "component"}
    df = df.rename(columns=rename)
    df = _make_columns_unique(df)
    return pl.from_pandas(df)


def dynamic_pypsa_to_polars(dynamic_df: pd.DataFrame, column_sep: str = "__") -> pl.DataFrame:
    """
    Convert PyPSA dynamic DataFrame (index = time/snapshots, columns = MultiIndex (scenario, component))
    to Polars with columns [time_step, scenario__component_1, scenario__component_2, ...].
    """
    if dynamic_df.empty:
        return pl.DataFrame()
    df = dynamic_df.copy()
    if isinstance(df.columns, pd.MultiIndex):
        df = df.set_axis(_flatten_multiindex_columns(df.columns, sep=column_sep), axis=1)
    df = df.reset_index()
    if df.columns[0] != "time_step" and df.columns[0] != "index":
        df = df.rename(columns={df.columns[0]: "time_step"})
    elif df.columns[0] == "index":
        df = df.rename(columns={"index": "time_step"})
    df = _make_columns_unique(df)
    return pl.from_pandas(df)


def dynamic_dict_pypsa_to_polars(
    dynamic_dict: dict[str, pd.DataFrame], column_sep: str = "__"
) -> dict[str, pl.DataFrame]:
    """Convert dict of PyPSA dynamic DataFrames to dict of Polars DataFrames."""
    return {key: dynamic_pypsa_to_polars(df, column_sep=column_sep) for key, df in dynamic_dict.items()}
