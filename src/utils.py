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
import re
import shutil
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


def set_pypsa_scenario_weights(
    network: Network,
    weights: dict[str, float] | None = None,
) -> dict[str, float]:
    """
    Set PyPSA scenario probabilities (``network.scenario_weightings``), normalized to sum 1.

    When *weights* is omitted, each scenario receives an equal share (e.g. 0.5 / 0.5 for two, 1/3 for three).

    Example with three scenarios::

        set_pypsa_scenario_weights(network, {"dry": 0.2, "normal": 0.5, "wet": 0.3})
    """
    if not (hasattr(network, "has_scenarios") and network.has_scenarios):
        raise ValueError("Network has no scenarios")
    scenarios = list(network.scenarios)
    if weights is None:
        share = 1.0 / len(scenarios)
        weights = {str(s): share for s in scenarios}
    unknown = set(weights) - set(scenarios)
    if unknown:
        raise ValueError(f"Unknown scenario(s) in weights: {sorted(unknown)}")
    total = sum(float(weights[str(s)]) for s in scenarios)
    if total <= 0:
        raise ValueError("Scenario weights must sum to a positive value")
    normalized = {str(s): float(weights[str(s)]) / total for s in scenarios}
    for scenario, weight in normalized.items():
        network.scenario_weightings.loc[scenario, "weight"] = weight
    return normalized


def write_pypsa_scenario_weights_manifest(
    study_root: Path,
    scenario_weightings: dict[str, float],
    *,
    scenario_order: Sequence[str] | None = None,
) -> Path:
    """Persist PyPSA scenario weights and MC-year order next to Xpansion settings."""
    order = [str(s) for s in scenario_order] if scenario_order is not None else sorted(scenario_weightings)
    manifest = study_root / "user" / "expansion" / "pypsa_scenario_weights.json"
    manifest.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "scenario_order": order,
        "scenarios": {name: float(scenario_weightings[name]) for name in order},
    }
    manifest.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    return manifest


def read_pypsa_scenario_weights_manifest(study_root: Path) -> tuple[dict[str, float], list[str]]:
    """Load scenario weights and order written at conversion time."""
    manifest = study_root / "user" / "expansion" / "pypsa_scenario_weights.json"
    if not manifest.is_file():
        raise FileNotFoundError(f"Missing scenario weights manifest: {manifest}")
    payload = json.loads(manifest.read_text(encoding="utf-8"))
    order = [str(s) for s in payload["scenario_order"]]
    weights = {str(k): float(v) for k, v in payload["scenarios"].items()}
    return weights, order


def _subproblem_mc_year_index(mps_path: Path) -> int:
    """Extract MC-year index from ``problem-{year}-1--optim-nb-1.mps`` (Antares PG with playlist)."""
    match = re.search(r"problem-(\d+)-\d+--", mps_path.name)
    if match:
        return int(match.group(1)) - 1
    return 0


def _find_xpansion_subproblem_mps(output_dir: Path) -> list[Path]:
    subproblems = list(output_dir.glob("problem-*.mps"))
    if not subproblems:
        subproblems = list(output_dir.glob("**/problem-*.mps"))
    return sorted(subproblems, key=_subproblem_mc_year_index)


def configure_xpansion_slave_weights(
    output_dir: Path,
    options_path: Path,
    scenario_weightings: dict[str, float],
    *,
    scenario_order: Sequence[str] | None = None,
) -> str:
    """
    Map PyPSA scenario probabilities to Antares Xpansion Benders ``SLAVE_WEIGHT``.

    - Equal weights (e.g. 0.5/0.5 or 1/3 each): ``UNIFORM`` (matches PyPSA in e2e tests).
    - Unequal weights: ``xpansion_slave_weights.txt`` with one ``<mps_path> <weight>`` line per
      subproblem plus ``WEIGHT_SUM 1``.

    Requires one operational subproblem per PyPSA scenario (``nbyears`` + playlist in study setup).
    Subproblems are sorted by MC year index in the filename; scenarios follow *scenario_order*.
    """
    total = sum(float(v) for v in scenario_weightings.values())
    if total <= 0:
        raise ValueError("Scenario weights must sum to a positive value")
    normalized = {str(k): float(v) / total for k, v in scenario_weightings.items()}
    if scenario_order is not None:
        order = [str(s) for s in scenario_order]
        missing = set(order) - set(normalized)
        if missing:
            raise ValueError(f"scenario_order contains unknown scenario(s): {sorted(missing)}")
        if len(order) != len(normalized):
            raise ValueError("scenario_order must list each scenario exactly once")
    else:
        order = sorted(normalized.keys())
    weight_values = [normalized[s] for s in order]

    subproblems = _find_xpansion_subproblem_mps(output_dir)
    if len(subproblems) != len(order):
        raise RuntimeError(
            f"Cannot map {len(order)} scenario(s) to {len(subproblems)} subproblem MPS file(s) under {output_dir}. "
            "Check problem-generator output or pass scenario_order matching Antares subproblem order."
        )

    mapping = [
        {
            "mc_year_index": _subproblem_mc_year_index(mps),
            "scenario": scenario,
            "subproblem": mps.name,
            "weight": normalized[scenario],
        }
        for mps, scenario in zip(subproblems, order, strict=True)
    ]
    (output_dir / "xpansion_slave_weights_mapping.json").write_text(
        json.dumps({"assignments": mapping}, indent=2),
        encoding="utf-8",
    )

    options = json.loads(options_path.read_text(encoding="utf-8"))
    equal_weights = len(weight_values) > 0 and max(weight_values) - min(weight_values) < 1e-9

    if equal_weights:
        options["SLAVE_WEIGHT"] = "UNIFORM"
        options.pop("SLAVE_WEIGHT_VALUE", None)
        mode = "UNIFORM"
    else:
        weights_file = output_dir / "xpansion_slave_weights.txt"
        lines = [
            f"./{mps.relative_to(output_dir).as_posix()} {normalized[scenario]}"
            for mps, scenario in zip(subproblems, order, strict=True)
        ]
        lines.append(f"WEIGHT_SUM {sum(normalized.values()):.12g}")
        weights_file.write_text("\n".join(lines) + "\n", encoding="utf-8")
        options["SLAVE_WEIGHT"] = weights_file.name
        options.pop("SLAVE_WEIGHT_VALUE", None)
        mode = weights_file.name

    options_path.write_text(json.dumps(options, indent=2), encoding="utf-8")
    source_options = output_dir.parent.parent / "user" / "expansion" / "options.json"
    if source_options.is_file():
        source_options.write_text(json.dumps(options, indent=2), encoding="utf-8")
    return mode


def prepare_benders_runtime_files(study_root: Path) -> tuple[Path, Path]:
    """
    # This function prepares the necessary runtime files for running the Antares Xpansion Benders algorithm.
    # It locates the output directory generated by Antares (ending with '*eco'), then copies the options.json
    # file used for the expansion from the study settings to the appropriate output directory.
    # It also ensures that the required directory structure exists, creates an empty area.txt file as needed,
    # and finally returns the paths to the output directory and the copied options.json file.
    # This setup is required so that the Benders binary can be executed with the expected file structure and options.
    """
    output_dirs = list((study_root / "output").glob("*eco")) + list((study_root / "output").glob("*exp"))
    if not output_dirs:
        raise FileNotFoundError(f"No Antares output directory found under {study_root / 'output'}")

    output_dir = max(output_dirs)
    source_options = study_root / "user" / "expansion" / "options.json"
    if not source_options.exists():
        raise FileNotFoundError(f"Missing Benders options file: {source_options}")

    runtime_options = output_dir / "options.json"
    (output_dir / "expansion").mkdir(parents=True, exist_ok=True)
    shutil.copy2(source_options, runtime_options)
    (output_dir / "area.txt").touch(exist_ok=True)
    return output_dir, runtime_options


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
