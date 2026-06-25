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
import subprocess
import time
from pathlib import Path
from typing import Any

import matplotlib.pyplot as plt
import pandas as pd
from pypsa import Network

# Project root: tests/utils.py -> parents[1] = project root
PROJECT_ROOT = Path(__file__).resolve().parents[1]


def load_pypsa_study(file: str, load_scaling: float) -> Network:
    """
    Load a PyPSA study from a NetCDF file, preparing it for analysis or manipulation.
    """
    input_file = PROJECT_ROOT / "resources" / "test_files" / file

    network = Network(input_file)

    # Scale the load to make the test case feasible
    network = scale_load(network, load_scaling)

    return network


def load_pypsa_study_benchmark(file: str, load_scaling: float) -> tuple[Network, float]:
    """
    Load a PyPSA study from a NetCDF file, preparing it for analysis or manipulation.
    """
    input_file = PROJECT_ROOT / "resources" / "test_files" / file

    start_time = time.time()
    network = Network(input_file)
    end_time = time.time() - start_time
    # Scale the load to make the test case feasible
    network = scale_load(network, load_scaling)

    return (network, end_time)


def scale_load(network: Network, factor: float) -> Network:
    network.loads_t["p_set"] *= factor
    return network


def extend_quota(network: Network) -> Network:
    # Temporary function, used while the GlobalConstraint model is not implemented yet.
    # Set the CO2 bound to very large value
    if not network.global_constraints.empty and "constant" in network.global_constraints.columns:
        network.global_constraints.loc[network.global_constraints.index[0], "constant"] = 10_000_000_000
    return network


def preprocess_network(network: Network, quota: bool) -> Network:
    if quota:
        network = extend_quota(network)
    return network


def enable_pypsa_solver_console_logging() -> None:
    """Enable PyPSA default solver console logging (``params.optimize.log_to_console``)."""
    import pypsa

    pypsa.options.params.optimize.log_to_console = True


def run_logged_subprocess(
    cmd: list[str],
    *,
    cwd: str | Path | None = None,
    logger: logging.Logger | None = None,
) -> subprocess.CompletedProcess[str]:
    """Run a command, streaming merged stdout/stderr to the logger line by line."""
    log = logger or logging.getLogger(__name__)
    log.info("Running: %s (cwd=%s)", " ".join(cmd), cwd or ".")
    stdout_lines: list[str] = []
    process = subprocess.Popen(
        cmd,
        cwd=str(cwd) if cwd is not None else None,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        bufsize=1,
    )
    if process.stdout is None:
        msg = f"Failed to start process: {' '.join(cmd)}"
        raise RuntimeError(msg)
    for line in process.stdout:
        stripped = line.rstrip("\n")
        stdout_lines.append(stripped)
        log.info("%s", stripped)
    returncode = process.wait()
    combined = "\n".join(stdout_lines)
    return subprocess.CompletedProcess(cmd, returncode, combined, "")


def resolve_project_root(start: Path | None = None) -> Path:
    """Find repository root (works from tests/, notebook cwd, or project root)."""
    start = start or Path.cwd()
    for directory in (start, *start.parents):
        if (directory / "pyproject.toml").is_file():
            return directory
    return PROJECT_ROOT


def benchmark_numeric(value: Any, default: float = 0) -> float:
    """Coerce a CSV cell to float for display; use default if missing or invalid."""
    v = pd.to_numeric(value, errors="coerce")
    return default if pd.isna(v) else float(v)


def get_results_path() -> Path:
    """Path to the Antares modeler benchmark CSV."""
    return resolve_project_root() / "tmp" / "benchmark_results" / "all_studies_results.csv"


def get_xpansion_results_path() -> Path:
    """Path to the PyPSA vs Antares Xpansion benchmark CSV."""
    return resolve_project_root() / "tmp" / "benchmark_results" / "xpansion_benchmark_results.csv"


def load_benchmark_results(results_file: Path | None = None) -> pd.DataFrame:
    path = results_file or get_results_path()
    if not path.exists():
        raise FileNotFoundError(f"Results file not found: {path}")
    return pd.read_csv(path)


def load_xpansion_benchmark_results(results_file: Path | None = None) -> pd.DataFrame:
    path = results_file or get_xpansion_results_path()
    if not path.exists():
        raise FileNotFoundError(f"Results file not found: {path}")
    return pd.read_csv(path)


def list_benchmark_studies(
    df: pd.DataFrame | None = None,
    results_file: Path | None = None,
) -> pd.DataFrame:
    """Print available Antares modeler benchmark rows and return the underlying dataframe."""
    if df is None:
        df = load_benchmark_results(results_file)
    path = results_file or get_results_path()
    print(f"Results file: {path}")
    print(f"Total studies: {len(df)}\n")
    for idx in range(len(df)):
        print(f"  Row {idx}: {benchmark_study_label(df.iloc[idx])}")
    return df


def list_xpansion_benchmark_studies(
    df: pd.DataFrame | None = None,
    results_file: Path | None = None,
) -> pd.DataFrame:
    """Print available Xpansion benchmark rows and return the underlying dataframe."""
    if df is None:
        df = load_xpansion_benchmark_results(results_file)
    path = results_file or get_xpansion_results_path()
    print(f"Results file: {path}")
    print(f"Total studies: {len(df)}\n")
    for idx in range(len(df)):
        print(f"  Row {idx}: {benchmark_study_label(df.iloc[idx])}")
    return df


MODELER_BENCHMARK_NUMERIC_COLS = [
    "parsing_time",
    "number_of_time_steps",
    "number_of_buses",
    "number_of_generators",
    "number_of_loads",
    "number_of_links",
    "number_of_storage_units",
    "number_of_stores",
    "number_of_lines",
    "number_of_transformers",
    "number_of_shunt_impedances",
    "preprocessing_time_pypsa_network",
    "pypsa_to_gems_conversion_time",
    "build_optimization_problem_time_pypsa",
    "pypsa_optimization_time",
    "total_time_pypsa",
    "modeler_parsing_time",
    "modeler_build_time",
    "modeler_solve_time",
    "modeler_writing_time",
    "modeler_total_time",
    "gemspy_parsing_time",
    "gemspy_build_time",
    "gemspy_solve_time",
    "gemspy_writing_time",
    "gemspy_total_time",
    "number_of_constraints_pypsa",
    "number_of_constraints_modeler",
    "number_of_constraints_gemspy",
    "number_of_variables_pypsa",
    "number_of_variables_modeler",
    "number_of_variables_gemspy",
    "pypsa_objective",
    "modeler_objective_value",
    "gemspy_objective_value",
]

XPANSION_BENCHMARK_NUMERIC_COLS = [
    "parsing_time",
    "number_of_time_steps",
    "preprocessing_time_pypsa_network",
    "pypsa_build_seconds",
    "pypsa_solve_seconds",
    "pypsa_total_objective",
    "number_of_variables_pypsa",
    "number_of_constraints_pypsa",
    "pypsa_to_gems_conversion_time",
    "xpansion_launcher_seconds",
    # Legacy columns kept so older problem-generator + Benders CSVs still load.
    "xpansion_problem_generator_seconds",
    "number_of_variables_xpansion",
    "number_of_constraints_xpansion",
    "xpansion_benders_seconds",
    "xpansion_overall_cost",
    "xpansion_run_duration_seconds",
]


def _xpansion_run_seconds(row: pd.Series) -> float:
    """Antares-Xpansion execution time: launcher timing, falling back to legacy PG + Benders."""
    launcher = benchmark_numeric(row.get("xpansion_launcher_seconds"))
    if launcher:
        return launcher
    return benchmark_numeric(row.get("xpansion_problem_generator_seconds")) + benchmark_numeric(
        row.get("xpansion_benders_seconds")
    )


def _coerce_benchmark_row(row: pd.Series, numeric_cols: list[str]) -> pd.Series:
    out = row.copy()
    for col in numeric_cols:
        if col in out.index:
            out[col] = pd.to_numeric(out[col], errors="coerce")
    return out


def get_modeler_benchmark_row(row_number: int, results_file: Path | None = None) -> pd.Series:
    df_all = load_benchmark_results(results_file)
    if row_number < 0 or row_number >= len(df_all):
        raise ValueError(f"Row number must be between 0 and {len(df_all) - 1}. Total studies: {len(df_all)}")
    return _coerce_benchmark_row(df_all.iloc[row_number], MODELER_BENCHMARK_NUMERIC_COLS)


def get_xpansion_benchmark_row(row_number: int, results_file: Path | None = None) -> pd.Series:
    df_all = load_xpansion_benchmark_results(results_file)
    if row_number < 0 or row_number >= len(df_all):
        raise ValueError(f"Row number must be between 0 and {len(df_all) - 1}. Total studies: {len(df_all)}")
    return _coerce_benchmark_row(df_all.iloc[row_number], XPANSION_BENCHMARK_NUMERIC_COLS)


def benchmark_study_label(row: pd.Series) -> str:
    """Human-readable study name for prints and plot titles (no raw .nc paths)."""
    network_name = row.get("pypsa_network_name")
    if pd.notna(network_name) and str(network_name).strip() and str(network_name) != "Unnamed Network":
        return str(network_name)
    filename = str(row.get("pypsa_filename", ""))
    if filename == "__tiny_synthetic__":
        return "Tiny 2-scenario (synthetic)"
    if filename and filename not in ("N/A", "nan"):
        return Path(filename).stem.replace("_", " ")
    return "Benchmark study"


def print_modeler_benchmark_summary(row: pd.Series, row_number: int = 0) -> None:
    """Print text summary for an Antares modeler benchmark row (PyPSA / Antares Modeler / GemsPy)."""
    _n = benchmark_numeric

    print("=" * 80)
    print(f"BENCHMARK ANALYSIS - STUDY ROW {row_number}")
    print("=" * 80)

    print("\n📊 NETWORK INFORMATION:")
    print(f"  Study: {benchmark_study_label(row)}")
    print(f"  Number of Time Steps: {int(_n(row.get('number_of_time_steps')))}")
    print(f"  PyPSA Version: {row.get('pypsa_version', 'N/A')}")
    print(f"  Antares Version: {row.get('antares_version', 'N/A')}")

    print("\n🔧 NETWORK COMPONENTS:")
    print(f"  Buses: {int(_n(row.get('number_of_buses')))}")
    print(f"  Generators: {int(_n(row.get('number_of_generators')))}")
    print(f"  Loads: {int(_n(row.get('number_of_loads')))}")
    print(f"  Links: {int(_n(row.get('number_of_links')))}")
    print(f"  Storage Units: {int(_n(row.get('number_of_storage_units')))}")
    print(f"  Stores: {int(_n(row.get('number_of_stores')))}")
    print(f"  Lines: {int(_n(row.get('number_of_lines')))}")
    print(f"  Transformers: {int(_n(row.get('number_of_transformers')))}")
    print(f"  Shunt Impedances: {int(_n(row.get('number_of_shunt_impedances')))}")

    print("\n⏱️  TIMING INFORMATION:")
    print(f"  Parsing Time (PyPSA .nc load): {_n(row.get('parsing_time')):.4f} s")
    print(f"  Preprocessing Time (PyPSA): {_n(row.get('preprocessing_time_pypsa_network')):.4f} s")
    print(f"  PyPSA to GEMS Conversion Time: {_n(row.get('pypsa_to_gems_conversion_time')):.4f} s")
    print(f"  Build Optimization Problem Time (PyPSA): {_n(row.get('build_optimization_problem_time_pypsa')):.4f} s")
    print(f"  PyPSA Optimization Time: {_n(row.get('pypsa_optimization_time')):.4f} s")
    print(f"  PyPSA Total Time: {_n(row.get('total_time_pypsa')):.4f} s")
    modeler_parsing = _n(row.get("modeler_parsing_time"))
    modeler_writing = _n(row.get("modeler_writing_time"))
    print(f"  Antares Modeler Parsing Time (YAML load): {modeler_parsing:.4f} s")
    print(f"  Antares Modeler Build Time: {_n(row.get('modeler_build_time')):.4f} s")
    print(f"  Antares Modeler Solve Time: {_n(row.get('modeler_solve_time')):.4f} s")
    print(f"  Antares Modeler Writing Time (simulation table): {modeler_writing:.4f} s")
    print(f"  Antares Modeler (parsing+build+solve+writing) Time: {_n(row.get('modeler_total_time')):.4f} s")
    gemspy_total = _n(row.get("gemspy_total_time"))
    if gemspy_total:
        print(f"  GemsPy Parsing Time (study+config): {_n(row.get('gemspy_parsing_time')):.4f} s")
        print(f"  GemsPy Build Time: {_n(row.get('gemspy_build_time')):.4f} s")
        print(f"  GemsPy Solve Time: {_n(row.get('gemspy_solve_time')):.4f} s")
        print(f"  GemsPy Writing Time (simulation table): {_n(row.get('gemspy_writing_time')):.4f} s")
        print(f"  GemsPy (parsing+build+solve+writing) Time: {gemspy_total:.4f} s")
    preproc = _n(row.get("preprocessing_time_pypsa_network"))
    conversion = _n(row.get("pypsa_to_gems_conversion_time"))
    full_modeler_path = preproc + conversion + _n(row.get("modeler_total_time"))
    print(
        f"  Antares Modeler, including conversion time (preproc + conversion + parsing + build + solve + writing): {full_modeler_path:.4f} s"
    )
    if gemspy_total:
        print(
            f"  Full GemsPy Path (preproc + conversion + parsing + build + solve + writing): {preproc + conversion + gemspy_total:.4f} s"
        )

    has_modeler_stats = "number_of_constraints_modeler" in row.index and "number_of_variables_modeler" in row.index
    n_const_pypsa = _n(row.get("number_of_constraints_pypsa"))
    n_var_pypsa = _n(row.get("number_of_variables_pypsa"))
    print("\n📈 OPTIMIZATION PROBLEM SIZE:")
    print(f"  PyPSA Constraints: {int(n_const_pypsa)}")
    print(f"  PyPSA Variables: {int(n_var_pypsa)}")
    n_const_gemspy = _n(row.get("number_of_constraints_gemspy"))
    n_var_gemspy = _n(row.get("number_of_variables_gemspy"))
    if n_const_gemspy or n_var_gemspy:
        print(f"  GemsPy Constraints: {int(n_const_gemspy)}")
        print(f"  GemsPy Variables: {int(n_var_gemspy)}")
    if has_modeler_stats:
        n_const_modeler = _n(row.get("number_of_constraints_modeler"))
        n_var_modeler = _n(row.get("number_of_variables_modeler"))
        print(f"  Antares Modeler Constraints: {int(n_const_modeler)}")
        if n_const_modeler:
            print(f"  Constraints Ratio (PyPSA/Antares Modeler): {n_const_pypsa / n_const_modeler:.4f}")
        else:
            print("  Constraints Ratio (PyPSA/Antares Modeler): N/A")
        print(f"  Antares Modeler Variables: {int(n_var_modeler)}")
        if n_var_modeler:
            print(f"  Variables Ratio (PyPSA/Antares Modeler): {n_var_pypsa / n_var_modeler:.4f}")
        else:
            print("  Variables Ratio (PyPSA/Antares Modeler): N/A")
    else:
        print("  Antares Modeler constraints/variables: N/A (not reported by this Antares version)")

    pypsa_obj = _n(row.get("pypsa_objective"))
    modeler_obj = _n(row.get("modeler_objective_value"))
    gemspy_obj = _n(row.get("gemspy_objective_value"))
    print("\n🎯 OBJECTIVE VALUES:")
    print(f"  PyPSA Objective: {pypsa_obj:.6f}")
    print(f"  Antares Modeler Objective: {modeler_obj:.6f}")
    if gemspy_obj:
        print(f"  GemsPy Objective: {gemspy_obj:.6f}")
    obj_diff = pypsa_obj - modeler_obj
    obj_diff_pct = (obj_diff / modeler_obj) * 100 if modeler_obj else 0.0
    print(f"  Difference (PyPSA - Antares Modeler): {obj_diff:.6f} ({obj_diff_pct:+.4f}%)")

    print("\n⚙️  SOLVER INFORMATION:")
    print(f"  PyPSA Solver: {row.get('solver_name_pypsa', 'N/A')} {row.get('solver_version_pypsa', '')}")
    print(f"  Antares Modeler Solver: {row.get('modeler_solver_name', 'N/A')}")
    print(f"  Antares Modeler Solver Parameters: {row.get('modeler_solver_parameters', 'N/A')}")
    if "gemspy_solver_name" in row.index:
        print(f"  GemsPy Solver: {row.get('gemspy_solver_name', 'N/A')}")
        print(f"  GemsPy Solver Parameters: {row.get('gemspy_solver_parameters', 'N/A')}")

    total_pypsa = _n(row.get("total_time_pypsa"))
    total_modeler = _n(row.get("modeler_total_time"))
    print("\n📊 PERFORMANCE COMPARISON:")
    time_ratio_binary = total_pypsa / total_modeler if total_modeler else float("nan")
    print(f"  Time Ratio PyPSA / Antares Modeler (parsing+build+solve+writing): {time_ratio_binary:.4f}x")
    time_ratio_full = total_pypsa / full_modeler_path if full_modeler_path else float("nan")
    print(f"  Time Ratio PyPSA / Antares Modeler, including conversion time: {time_ratio_full:.4f}x")
    if pd.notna(time_ratio_binary) and time_ratio_binary > 0:
        if time_ratio_binary < 1:
            print(f"  → PyPSA is {1 / time_ratio_binary:.2f}x faster (vs Antares Modeler)")
        else:
            print(f"  → Antares Modeler is {time_ratio_binary:.2f}x faster")
    if pd.notna(time_ratio_full) and time_ratio_full > 0:
        if time_ratio_full < 1:
            print(f"  → PyPSA is {1 / time_ratio_full:.2f}x faster (vs Antares Modeler, including conversion time)")
        else:
            print(f"  → Antares Modeler, including conversion time is {time_ratio_full:.2f}x faster")
    if pd.isna(time_ratio_binary) and pd.isna(time_ratio_full):
        print("  → N/A (missing or invalid times)")

    print("\n" + "=" * 80)


def print_xpansion_benchmark_summary(row: pd.Series, row_number: int = 0) -> None:
    """Print text summary for a PyPSA vs Antares Xpansion benchmark row."""
    _n = benchmark_numeric

    print("=" * 80)
    print(f"XPANSION BENCHMARK - STUDY ROW {row_number}")
    print("=" * 80)

    print("\n📊 STUDY INFORMATION:")
    print(f"  Study: {benchmark_study_label(row)}")
    print(f"  Time steps: {int(_n(row.get('number_of_time_steps')))}")
    print(f"  PyPSA version: {row.get('pypsa_version', 'N/A')}")
    print(f"  Antares version: {row.get('antares_version', 'N/A')}")
    print(f"  Antares Xpansion version: {row.get('antares_xpansion_version', 'N/A')}")

    print("\n⏱️  TIMING:")
    parsing = _n(row.get("parsing_time"))
    preproc = _n(row.get("preprocessing_time_pypsa_network"))
    conversion = _n(row.get("pypsa_to_gems_conversion_time"))
    pypsa_build = _n(row.get("pypsa_build_seconds"))
    pypsa_solve = _n(row.get("pypsa_solve_seconds"))
    launcher_time = _xpansion_run_seconds(row)
    pypsa_total = parsing + preproc + pypsa_build + pypsa_solve
    xpansion_total = preproc + conversion + launcher_time
    print(f"  Parsing (.nc / synthetic): {parsing:.4f} s")
    print(f"  Preprocessing: {preproc:.4f} s")
    print(f"  PyPSA build: {pypsa_build:.4f} s")
    print(f"  PyPSA solve: {pypsa_solve:.4f} s")
    print(f"  PyPSA path total (parse+preproc+build+solve): {pypsa_total:.4f} s")
    print(f"  Conversion to GEMS study: {conversion:.4f} s")
    print(f"  Xpansion launcher (problem-generation + Benders): {launcher_time:.4f} s")
    print(f"  Xpansion path total (preproc+conversion+launcher): {xpansion_total:.4f} s")
    if row.get("xpansion_run_duration_seconds") is not None and not pd.isna(row.get("xpansion_run_duration_seconds")):
        print(f"  Xpansion reported run duration: {_n(row.get('xpansion_run_duration_seconds')):.4f} s")

    print("\n📈 PROBLEM SIZE:")
    n_var_pypsa = int(_n(row.get("number_of_variables_pypsa")))
    n_cons_pypsa = int(_n(row.get("number_of_constraints_pypsa")))
    n_var_xp = _n(row.get("number_of_variables_xpansion"))
    n_cons_xp = _n(row.get("number_of_constraints_xpansion"))
    print(f"  PyPSA variables: {n_var_pypsa}")
    print(f"  PyPSA constraints: {n_cons_pypsa}")
    if n_var_xp:
        print(f"  Antares Xpansion variables: {int(n_var_xp)}")
        if n_var_pypsa:
            print(f"  Variables ratio (PyPSA/Antares Xpansion): {n_var_pypsa / n_var_xp:.4f}")
    else:
        print("  Antares Xpansion variables: N/A")
    if n_cons_xp:
        print(f"  Antares Xpansion constraints: {int(n_cons_xp)}")
        if n_cons_pypsa:
            print(f"  Constraints ratio (PyPSA/Antares Xpansion): {n_cons_pypsa / n_cons_xp:.4f}")
    else:
        print("  Antares Xpansion constraints: N/A")

    pypsa_obj = _n(row.get("pypsa_total_objective"))
    xp_obj = _n(row.get("xpansion_overall_cost"))
    print("\n🎯 OBJECTIVES:")
    print(f"  PyPSA total objective: {pypsa_obj:.6f}")
    print(f"  Antares Xpansion overall cost: {xp_obj:.6f}")
    if xp_obj:
        diff = pypsa_obj - xp_obj
        print(f"  Difference (PyPSA - Antares Xpansion): {diff:.6f} ({(diff / xp_obj) * 100:+.4f}%)")
    print(f"  PyPSA status: {row.get('pypsa_status', 'N/A')} / {row.get('pypsa_condition', 'N/A')}")
    print(f"  Antares Xpansion problem status: {row.get('xpansion_problem_status', 'N/A')}")

    if pypsa_total and xpansion_total:
        ratio = pypsa_total / xpansion_total
        print("\n📊 PERFORMANCE:")
        print(f"  Time ratio PyPSA / Antares Xpansion (paths above): {ratio:.4f}x")
        if ratio < 1:
            print(f"  → PyPSA path is {1 / ratio:.2f}x faster")
        else:
            print(f"  → Antares Xpansion path is {ratio:.2f}x faster")

    print("\n" + "=" * 80)


def plot_xpansion_benchmark_study(row: pd.Series, row_number: int = 0) -> None:
    """Plot PyPSA vs Antares Xpansion comparison for one benchmark row."""
    _n = benchmark_numeric
    from matplotlib.gridspec import GridSpec

    pypsa_obj = _n(row.get("pypsa_total_objective"))
    xp_obj = _n(row.get("xpansion_overall_cost"))
    n_var_pypsa = int(_n(row.get("number_of_variables_pypsa")))
    n_cons_pypsa = int(_n(row.get("number_of_constraints_pypsa")))
    n_var_xp = int(_n(row.get("number_of_variables_xpansion")))
    n_cons_xp = int(_n(row.get("number_of_constraints_xpansion")))

    parsing = _n(row.get("parsing_time"))
    preproc = _n(row.get("preprocessing_time_pypsa_network"))
    conversion = _n(row.get("pypsa_to_gems_conversion_time"))
    pypsa_build = _n(row.get("pypsa_build_seconds"))
    pypsa_solve = _n(row.get("pypsa_solve_seconds"))
    launcher_time = _xpansion_run_seconds(row)

    study_label = benchmark_study_label(row)
    fig = plt.figure(figsize=(18, 11))
    size_header = (
        f"Variables — PyPSA: {n_var_pypsa:,}   |   Antares Xpansion: {n_var_xp:,}\n"
        f"Constraints — PyPSA: {n_cons_pypsa:,}   |   Antares Xpansion: {n_cons_xp:,}"
    )
    fig.text(
        0.5,
        0.98,
        f"PyPSA vs Antares Xpansion — {study_label}\n{size_header}",
        ha="center",
        va="top",
        fontsize=11,
        fontweight="bold",
    )
    gs = GridSpec(2, 3, figure=fig, top=0.86, hspace=0.45, wspace=0.35)
    categories = ["PyPSA", "Antares Xpansion"]
    colors = ["steelblue", "coral"]

    ax1 = fig.add_subplot(gs[0, 0])
    bars = ax1.bar(categories, [pypsa_obj, xp_obj], color=colors, alpha=0.7, edgecolor="black")
    ax1.set_title("Objective", fontweight="bold")
    ax1.set_ylabel("Cost")
    ax1.grid(True, alpha=0.3, axis="y")
    for bar, val in zip(bars, [pypsa_obj, xp_obj]):
        ax1.text(
            bar.get_x() + bar.get_width() / 2, bar.get_height(), f"{val:.2e}", ha="center", va="bottom", fontsize=9
        )

    ax2 = fig.add_subplot(gs[0, 1])
    stack_layers = [
        ("Parsing", parsing, 0.0),
        ("Preproc", preproc, preproc),
        ("Build / Convert", pypsa_build, conversion),
        ("Solve / Launcher", pypsa_solve, launcher_time),
    ]
    bottom = [0.0, 0.0]
    layer_colors = ["#2e86ab", "#5c7a29", "steelblue", "coral"]
    for i, (label, py_v, xp_v) in enumerate(stack_layers):
        ax2.bar(
            categories,
            [py_v, xp_v],
            bottom=bottom,
            label=label,
            color=layer_colors[i % len(layer_colors)],
            alpha=0.85,
            edgecolor="black",
        )
        bottom[0] += py_v
        bottom[1] += xp_v
    for i, total in enumerate(bottom):
        if total > 0:
            ax2.text(i, total, f"{total:.3f}s", ha="center", va="bottom", fontsize=9)
    ax2.set_title("Wall-clock breakdown", fontweight="bold")
    ax2.set_ylabel("Time (s)")
    ax2.legend(fontsize=8)
    ax2.grid(True, alpha=0.3, axis="y")

    ax3 = fig.add_subplot(gs[0, 2])
    cons_bars = ax3.bar(categories, [n_cons_pypsa, n_cons_xp], color=colors, alpha=0.7, edgecolor="black")
    ax3.set_title("Constraints", fontweight="bold")
    ax3.grid(True, alpha=0.3, axis="y")
    for bar, val in zip(cons_bars, [n_cons_pypsa, n_cons_xp]):
        ax3.text(bar.get_x() + bar.get_width() / 2, bar.get_height(), f"{val:,}", ha="center", va="bottom", fontsize=9)

    ax4 = fig.add_subplot(gs[1, 0])
    var_bars = ax4.bar(categories, [n_var_pypsa, n_var_xp], color=colors, alpha=0.7, edgecolor="black")
    ax4.set_title("Variables", fontweight="bold")
    ax4.grid(True, alpha=0.3, axis="y")
    for bar, val in zip(var_bars, [n_var_pypsa, n_var_xp]):
        ax4.text(bar.get_x() + bar.get_width() / 2, bar.get_height(), f"{val:,}", ha="center", va="bottom", fontsize=9)

    ax5 = fig.add_subplot(gs[1, 1])
    pypsa_layers = [("Parse", parsing), ("Preproc", preproc), ("Build", pypsa_build), ("Solve", pypsa_solve)]
    xp_layers = [("Preproc", preproc), ("Convert", conversion), ("Launcher", launcher_time)]
    py_pie = [(lbl, v) for lbl, v in pypsa_layers if v > 0]
    if py_pie:
        ax5.pie([v for _, v in py_pie], labels=[lbl for lbl, _ in py_pie], autopct="%1.1f%%", startangle=90)
    ax5.set_title("PyPSA time breakdown", fontweight="bold")

    ax6 = fig.add_subplot(gs[1, 2])
    xp_pie = [(lbl, v) for lbl, v in xp_layers if v > 0]
    if xp_pie:
        ax6.pie([v for _, v in xp_pie], labels=[lbl for lbl, _ in xp_pie], autopct="%1.1f%%", startangle=90)
    ax6.set_title("Antares Xpansion time breakdown", fontweight="bold")

    fig.tight_layout(rect=(0, 0, 1, 0.92))
    plt.show()


def analyze_xpansion_benchmark_study(
    row_number: int,
    results_file: Path | None = None,
    *,
    plot: bool = True,
    return_dataframe: bool = False,
) -> pd.DataFrame | None:
    """Print (and optionally plot) one row from the Xpansion benchmark CSV."""
    df_all = load_xpansion_benchmark_results(results_file)
    row = get_xpansion_benchmark_row(row_number, results_file)
    print_xpansion_benchmark_summary(row, row_number)
    if plot:
        plot_xpansion_benchmark_study(row, row_number)
    if return_dataframe:
        return df_all.iloc[[row_number]].copy()
    return None


def analyze_benchmark_study(
    row_number: int,
    results_file: Path | None = None,
    *,
    return_dataframe: bool = False,
) -> pd.DataFrame | None:
    """
    Analyze and plot benchmark results for a specific study.

    Parameters:
    -----------
    row_number : int
        Row number (0-indexed) of the study to analyze
    results_file : Path, optional
        Path to the results CSV file. If None, will try to find it automatically.
    """
    df_all = load_benchmark_results(results_file)
    row = get_modeler_benchmark_row(row_number, results_file)
    df = df_all.iloc[[row_number]].copy()
    _n = benchmark_numeric

    print_modeler_benchmark_summary(row, row_number)

    pypsa_obj = _n(row.get("pypsa_objective"))
    modeler_obj = _n(row.get("modeler_objective_value"))
    total_modeler = _n(row.get("modeler_total_time"))
    n_const_pypsa = _n(row.get("number_of_constraints_pypsa"))
    n_const_modeler = _n(row.get("number_of_constraints_modeler"))
    n_var_pypsa = _n(row.get("number_of_variables_pypsa"))
    n_var_modeler = _n(row.get("number_of_variables_modeler"))

    # Create visualizations: top row (4 plots) + bottom row (3 plots + wide end-to-end)
    fig = plt.figure(figsize=(20, 12))
    from matplotlib.gridspec import GridSpec

    gs = GridSpec(2, 8, figure=fig, hspace=0.6, wspace=0.5)

    # 1. Objective Value Comparison
    ax1 = fig.add_subplot(gs[0, 0:2])
    categories = ["PyPSA", "Antares Modeler", "GemsPy"]
    gemspy_obj = _n(row.get("gemspy_objective_value"))
    objectives = [pypsa_obj, modeler_obj, gemspy_obj]
    bars = ax1.bar(categories, objectives, color=["steelblue", "coral", "seagreen"], alpha=0.7, edgecolor="black")
    ax1.set_ylabel("Objective Value", fontsize=11)
    ax1.set_title("Objective Value Comparison", fontsize=12, fontweight="bold", pad=10)
    ax1.grid(True, alpha=0.3, axis="y")
    # Add value labels on bars
    for bar, val in zip(bars, objectives):
        height = bar.get_height()
        ax1.text(bar.get_x() + bar.get_width() / 2.0, height, f"{val:.2e}", ha="center", va="bottom", fontsize=9)

    # Used in plots below
    preproc_time = _n(row.get("preprocessing_time_pypsa_network"))
    conversion_time = _n(row.get("pypsa_to_gems_conversion_time"))
    modeler_parsing_time_plot = _n(row.get("modeler_parsing_time"))
    modeler_writing_time_plot = _n(row.get("modeler_writing_time"))
    # PyPSA parsing = loading the .nc file; tracked separately, NOT included in build time
    pypsa_parsing_time_plot = _n(row.get("parsing_time"))
    gemspy_parsing_time_plot = _n(row.get("gemspy_parsing_time"))
    gemspy_build = _n(row.get("gemspy_build_time"))
    gemspy_solve = _n(row.get("gemspy_solve_time"))
    gemspy_writing_time_plot = _n(row.get("gemspy_writing_time"))

    # 2. Total time: parsing+build+solve for PyPSA (no writing); parsing+build+solve+writing for Antares Modeler
    ax2 = fig.add_subplot(gs[0, 2:4])
    pypsa_build = _n(row["build_optimization_problem_time_pypsa"])
    pypsa_solve = _n(row["pypsa_optimization_time"])
    modeler_build = _n(row.get("modeler_build_time"))
    modeler_solve = _n(row.get("modeler_solve_time"))
    if modeler_build == 0 and modeler_solve == 0 and total_modeler > 0:
        modeler_build = 0.0
        modeler_solve = total_modeler

    # PyPSA: parsing (.nc load) / build / solve — no writing (results stay in-memory)
    # Antares Modeler: parsing (YAML load) / build / solve / writing (simulation table to disk)
    bottom_pypsa: float = 0.0
    bottom_modeler: float = 0.0
    bottom_gemspy: float = 0.0
    layer_defs = [
        ("Parsing", pypsa_parsing_time_plot, modeler_parsing_time_plot, gemspy_parsing_time_plot, "#2e86ab"),
        ("Build", pypsa_build, modeler_build, gemspy_build, "steelblue"),
        ("Solve", pypsa_solve, modeler_solve, gemspy_solve, "coral"),
        ("Writing", 0.0, modeler_writing_time_plot, gemspy_writing_time_plot, "#f18f01"),
    ]
    bar_refs = []
    for label, pypsa_val, mod_val, gem_val, color in layer_defs:
        b = ax2.bar(
            ["PyPSA", "Antares Modeler", "GemsPy"],
            [pypsa_val, mod_val, gem_val],
            bottom=[bottom_pypsa, bottom_modeler, bottom_gemspy],
            label=label,
            color=color,
            alpha=0.8,
            edgecolor="black",
        )
        bar_refs.append(b)
        bottom_pypsa += pypsa_val
        bottom_modeler += mod_val
        bottom_gemspy += gem_val

    ax2.set_ylabel("Time (seconds)", fontsize=11)
    ax2.set_title("Time Comparison (Build + Solve)", fontsize=12, fontweight="bold", pad=10)
    ax2.grid(True, alpha=0.3, axis="y")
    for i, total_h in enumerate([bottom_pypsa, bottom_modeler, bottom_gemspy]):
        if total_h > 0:
            ax2.text(i, total_h, f"{total_h:.3f}s", ha="center", va="bottom", fontsize=9)
    ax2.legend(fontsize=9)

    # 3. Constraints Comparison
    ax3 = fig.add_subplot(gs[0, 4:6])
    n_const_gemspy = _n(row.get("number_of_constraints_gemspy"))
    constraints = [int(n_const_pypsa), int(n_const_modeler), int(n_const_gemspy)]
    bars = ax3.bar(categories, constraints, color=["steelblue", "coral", "seagreen"], alpha=0.7, edgecolor="black")
    ax3.set_ylabel("Number of Constraints", fontsize=11)
    ax3.set_title("Constraints Comparison", fontsize=12, fontweight="bold", pad=10)
    ax3.grid(True, alpha=0.3, axis="y")
    for bar, val in zip(bars, constraints):
        height = bar.get_height()
        ax3.text(bar.get_x() + bar.get_width() / 2.0, height, f"{val:,}", ha="center", va="bottom", fontsize=9)

    # 4. Variables Comparison
    ax4 = fig.add_subplot(gs[0, 6:8])
    n_var_gemspy = _n(row.get("number_of_variables_gemspy"))
    variables = [int(n_var_pypsa), int(n_var_modeler), int(n_var_gemspy)]
    bars = ax4.bar(categories, variables, color=["steelblue", "coral", "seagreen"], alpha=0.7, edgecolor="black")
    ax4.set_ylabel("Number of Variables", fontsize=11)
    ax4.set_title("Variables Comparison", fontsize=12, fontweight="bold", pad=10)
    ax4.grid(True, alpha=0.3, axis="y")
    for bar, val in zip(bars, variables):
        height = bar.get_height()
        ax4.text(bar.get_x() + bar.get_width() / 2.0, height, f"{val:,}", ha="center", va="bottom", fontsize=9)

    # 5. PyPSA Time Breakdown (parsing / preprocessing / build / solve) — no writing phase
    ax5 = fig.add_subplot(gs[1, 0:2])
    pypsa_pie_labels = ["Parsing (.nc)", "Preprocessing", "Build", "Solve"]
    pypsa_pie_vals = [
        pypsa_parsing_time_plot,
        preproc_time,
        _n(row["build_optimization_problem_time_pypsa"]),
        _n(row["pypsa_optimization_time"]),
    ]
    pypsa_pie_colors = ["#2e86ab", "#5c7a29", "steelblue", "coral"]
    filtered_pypsa = [(lbl, v, c) for lbl, v, c in zip(pypsa_pie_labels, pypsa_pie_vals, pypsa_pie_colors) if v > 0]
    if filtered_pypsa:
        fp_labels, fp_vals, fp_colors = zip(*filtered_pypsa)
        ax5.pie(fp_vals, labels=fp_labels, autopct="%1.1f%%", startangle=90, colors=fp_colors)
    else:
        ax5.text(0.5, 0.5, "No data", ha="center", va="center", transform=ax5.transAxes)
    ax5.set_title("PyPSA Time Breakdown", fontsize=12, fontweight="bold", pad=10)

    # 6. Antares Modeler Time Breakdown (parsing / build / solve / writing)
    ax6 = fig.add_subplot(gs[1, 2:4])
    _modeler_build = _n(row.get("modeler_build_time"))
    _modeler_solve = _n(row.get("modeler_solve_time"))
    if _modeler_build == 0 and _modeler_solve == 0 and total_modeler > 0:
        _modeler_build = 0.0
        _modeler_solve = total_modeler
    modeler_pie_labels = ["Parsing (YAML + systems)", "Build", "Solve", "Writing sim. table"]
    modeler_pie_vals = [modeler_parsing_time_plot, _modeler_build, _modeler_solve, modeler_writing_time_plot]
    modeler_pie_colors = ["#2e86ab", "steelblue", "coral", "#f18f01"]
    # Only include non-zero slices
    filtered = [(lbl, v, c) for lbl, v, c in zip(modeler_pie_labels, modeler_pie_vals, modeler_pie_colors) if v > 0]
    if filtered:
        f_labels, f_vals, f_colors = zip(*filtered)
        ax6.pie(f_vals, labels=f_labels, autopct="%1.1f%%", startangle=90, colors=f_colors)
    else:
        ax6.text(0.5, 0.5, "No data", ha="center", va="center", transform=ax6.transAxes)
    ax6.set_title("Antares Modeler Time Breakdown", fontsize=12, fontweight="bold", pad=10)

    # 7. GemsPy Time Breakdown (parsing / build / solve / writing)
    ax7 = fig.add_subplot(gs[1, 4:6])
    gemspy_pie_labels = ["Parsing (study+config)", "Build", "Solve", "Writing sim. table"]
    gemspy_pie_vals = [gemspy_parsing_time_plot, gemspy_build, gemspy_solve, gemspy_writing_time_plot]
    gemspy_pie_colors = ["#2e86ab", "steelblue", "coral", "#f18f01"]
    filtered_gemspy = [(lbl, v, c) for lbl, v, c in zip(gemspy_pie_labels, gemspy_pie_vals, gemspy_pie_colors) if v > 0]
    if filtered_gemspy:
        g_labels, g_vals, g_colors = zip(*filtered_gemspy)
        ax7.pie(g_vals, labels=g_labels, autopct="%1.1f%%", startangle=90, colors=g_colors)
    else:
        ax7.text(0.5, 0.5, "No data", ha="center", va="center", transform=ax7.transAxes)
    ax7.set_title("GemsPy Time Breakdown", fontsize=12, fontweight="bold", pad=10)

    # 8. End-to-end comparison: full PyPSA vs full Antares Modeler vs full GemsPy (stacked bars)
    # PyPSA:   Parsing (.nc) / Preprocessing / Build / Solve
    # Antares Modeler: Preprocessing / Conversion / Parsing (YAML) / Build / Solve / Writing sim. table
    # GemsPy:  Preprocessing / Conversion / Parsing (study+config) / Build / Solve / Writing sim. table
    ax8 = fig.add_subplot(gs[1, 6:8])
    e2e_layer_defs = [
        # (label,                 pypsa_val,                modeler_val,                 gemspy_val,               color)
        ("Parsing (.nc)", pypsa_parsing_time_plot, 0.0, 0.0, "#2e86ab"),
        ("Preprocessing", preproc_time, preproc_time, preproc_time, "#5c7a29"),
        ("Conversion", 0.0, conversion_time, conversion_time, "#a23b72"),
        ("Parsing (YAML/study)", 0.0, modeler_parsing_time_plot, gemspy_parsing_time_plot, "#4db6d0"),
        ("Build", pypsa_build, modeler_build, gemspy_build, "steelblue"),
        ("Solve", pypsa_solve, modeler_solve, gemspy_solve, "coral"),
        ("Writing sim. table", 0.0, modeler_writing_time_plot, gemspy_writing_time_plot, "#f18f01"),
    ]
    bot_pypsa: float = 0.0
    bot_modeler: float = 0.0
    bot_gemspy: float = 0.0
    for e2e_label, e2e_pypsa, e2e_mod, e2e_gem, e2e_color in e2e_layer_defs:
        ax8.bar(
            ["PyPSA", "Antares Modeler", "GemsPy"],
            [e2e_pypsa, e2e_mod, e2e_gem],
            bottom=[bot_pypsa, bot_modeler, bot_gemspy],
            label=e2e_label,
            color=e2e_color,
            alpha=0.8,
            edgecolor="black",
        )
        bot_pypsa += e2e_pypsa
        bot_modeler += e2e_mod
        bot_gemspy += e2e_gem
    for i, total_h in enumerate([bot_pypsa, bot_modeler, bot_gemspy]):
        if total_h > 0:
            ax8.text(i, total_h, f"{total_h:.3f}s", ha="center", va="bottom", fontsize=9)
    ax8.set_ylabel("Time (seconds)", fontsize=11)
    ax8.set_title("End-to-end Comparison (Full Path)", fontsize=12, fontweight="bold", pad=10)
    ax8.legend(fontsize=8, loc="upper left", bbox_to_anchor=(1.02, 1), borderaxespad=0)
    ax8.grid(True, alpha=0.3, axis="y")

    fig.suptitle(
        f"Benchmark Analysis - Study Row {row_number}: {benchmark_study_label(row)}",
        fontsize=14,
        fontweight="bold",
        y=0.995,
    )
    fig.tight_layout(rect=(0, 0, 0.97, 0.99))
    plt.show()

    if return_dataframe:
        return df
    return None


def get_objective_value(file_name: Path) -> float:
    match file_name.suffix:
        case ".csv":
            df = pd.read_csv(file_name, usecols=["output", "value"])
            result = df.query("output == 'OBJECTIVE_VALUE'")["value"]
            return float(result.iloc[0])
        case ".tsv":
            df = pd.read_csv(file_name, sep="\t", usecols=["output", "value"])
            result = df.query("output == 'OBJECTIVE_VALUE'")["value"]
            return float(result.iloc[0])
        case _:
            raise ValueError(f"Invalid file format: {file_name.suffix}")
