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
import subprocess
from collections.abc import Sequence
from pathlib import Path
from typing import Any, cast

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


def run_xpansion_launcher(
    study_root: Path,
    launcher_bin: Path,
    *,
    extra_args: Sequence[str] | None = None,
    logger: logging.Logger | None = None,
) -> subprocess.CompletedProcess[str]:
    """Run ``antares-xpansion-launcher -i <study_root>`` on a converter-generated GEMS study."""
    command = [str(launcher_bin), "-i", str(study_root)]
    if extra_args:
        command.extend(str(arg) for arg in extra_args)
    if logger is not None:
        logger.info("Running Antares-Xpansion launcher: %s", " ".join(command))
    return subprocess.run(
        command,
        capture_output=True,
        text=True,
        check=False,
        cwd=str(launcher_bin.parent),
    )


def read_xpansion_out_json(study_root: Path) -> dict[str, Any]:
    """Load the Antares-Xpansion ``out.json`` produced by the launcher under ``<study_root>/output``."""
    output_root = study_root / "output"
    out_json = next(output_root.glob("**/out.json"), None)
    if out_json is None:
        raise FileNotFoundError(f"No Antares-Xpansion out.json found under {output_root}")
    return cast(dict[str, Any], json.loads(out_json.read_text(encoding="utf-8")))


_MPS_NVARS_RE = re.compile(r"^\*\s*Number of variables:\s*(\d+)\s*$", re.MULTILINE)
_MPS_NCONS_RE = re.compile(r"^\*\s*Number of constraints:\s*(\d+)\s*$", re.MULTILINE)
_SLAVE_MPS_NAME_RE = re.compile(r"^\d+-\d+\.mps$")


def read_mps_problem_size(mps_path: Path) -> tuple[int, int]:
    """Return ``(n_variables, n_constraints)`` from an Antares MPSGenerator header."""
    # Only the comment header is needed; avoid loading multi-MB MPS bodies.
    header = mps_path.read_text(encoding="utf-8", errors="replace")[:4096]
    nvars_match = _MPS_NVARS_RE.search(header)
    ncons_match = _MPS_NCONS_RE.search(header)
    if nvars_match is None or ncons_match is None:
        raise ValueError(f"Could not parse variable/constraint counts from {mps_path}")
    return int(nvars_match.group(1)), int(ncons_match.group(1))


def read_xpansion_mps_sizes(study_root: Path) -> dict[str, int]:
    """
    Collect master + slave MPS sizes written under ``<study_root>/output/**/lp``.

    Requires the launcher to have been run with ``--keepMps``. Returns totals useful for
    comparing against PyPSA's monolithic ``nvars`` / ``ncons``, plus master/subproblem
    breakdowns.
    """
    lp_dirs = sorted((study_root / "output").glob("**/lp"))
    if not lp_dirs:
        raise FileNotFoundError(f"No Xpansion lp/ directory under {study_root / 'output'}")
    lp_dir = lp_dirs[-1]

    master_mps = lp_dir / "master.mps"
    if not master_mps.is_file():
        raise FileNotFoundError(f"No master.mps in {lp_dir} (run launcher with --keepMps)")

    master_vars, master_cons = read_mps_problem_size(master_mps)
    slave_vars_total = 0
    slave_cons_total = 0
    n_subproblems = 0
    for mps_path in sorted(lp_dir.glob("*.mps")):
        if not _SLAVE_MPS_NAME_RE.match(mps_path.name):
            continue
        nvars, ncons = read_mps_problem_size(mps_path)
        slave_vars_total += nvars
        slave_cons_total += ncons
        n_subproblems += 1

    return {
        "number_of_xpansion_subproblems": n_subproblems,
        "number_of_variables_xpansion_master": master_vars,
        "number_of_constraints_xpansion_master": master_cons,
        "number_of_variables_xpansion_subproblems": slave_vars_total,
        "number_of_constraints_xpansion_subproblems": slave_cons_total,
        "number_of_variables_xpansion": master_vars + slave_vars_total,
        "number_of_constraints_xpansion": master_cons + slave_cons_total,
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
