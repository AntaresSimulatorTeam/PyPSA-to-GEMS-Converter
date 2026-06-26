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
    """
    Run ``antares-xpansion-launcher -i <study_root>`` on a converter-generated GEMS study.

    The launcher auto-detects the GEMS workflow (problem-generation + Benders in one shot) when
    ``<study_root>/input/optim-config.yml`` is present, so no explicit ``--step gems`` is required.
    Returns the completed process; callers decide how to handle a non-zero return code.
    """
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
    """
    Load the Antares-Xpansion ``out.json`` produced by the launcher under ``<study_root>/output``.

    The launcher writes results in the most recent output run directory; the exact location
    (``expansion/out.json`` vs ``lp/out.json``) depends on the Xpansion version, so the newest
    ``out.json`` anywhere under ``output/`` is used.
    """
    output_root = study_root / "output"
    candidates = sorted(output_root.glob("**/out.json"), key=lambda path: path.stat().st_mtime)
    if not candidates:
        raise FileNotFoundError(f"No Antares-Xpansion out.json found under {output_root}")
    return cast(dict[str, Any], json.loads(candidates[-1].read_text(encoding="utf-8")))


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
