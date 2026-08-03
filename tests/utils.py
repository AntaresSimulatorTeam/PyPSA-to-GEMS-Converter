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

import re
import time
import tomllib
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
    if len(network.global_constraints) > 0 and "constant" in network.global_constraints.columns:
        network.global_constraints["constant"][0] = 10000000000
    return network


def preprocess_network(network: Network, quota: bool) -> Network:
    if quota:
        network = extend_quota(network)
    return network


def get_gemspy_version() -> str:
    """Return the GemsPy version pinned in pyproject.toml."""
    pyproject_path = PROJECT_ROOT / "pyproject.toml"
    with pyproject_path.open("rb") as f:
        data = tomllib.load(f)

    for dep in data.get("dependency-groups", {}).get("dev", []):
        if dep.startswith("gemspy"):
            match = re.match(r"gemspy==(.+)", dep)
            if match:
                return match.group(1)
    return "N/A"


def analyze_benchmark_study(row_number: int, results_file: Path | None = None) -> pd.DataFrame:
    """
    Analyze and plot benchmark results for a specific study.

    Parameters:
    -----------
    row_number : int
        Row number (0-indexed) of the study to analyze
    results_file : Path, optional
        Path to the results CSV file. If None, will try to find it automatically.
    """
    # Load data
    if results_file is None:
        results_file = get_results_path()

    if not results_file.exists():
        raise FileNotFoundError(f"Results file not found: {results_file}")

    df_all = pd.read_csv(results_file)

    if row_number < 0 or row_number >= len(df_all):
        raise ValueError(f"Row number must be between 0 and {len(df_all) - 1}. Total studies available: {len(df_all)}")

    df = df_all.iloc[[row_number]].copy()
    row = df.iloc[0]

    # Coerce numeric columns (CSV may have mixed types or strings)
    numeric_cols = [
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
        "pypsa_build_time",
        "pypsa_solve_time",
        "total_time_pypsa",
        "antares_modeler_parsing_time",
        "antares_modeler_build_time",
        "antares_modeler_solve_time",
        "antares_modeler_writing_time",
        "antares_modeler_total_time",
        "gemspy_parsing_time",
        "gemspy_build_time",
        "gemspy_solve_time",
        "gemspy_writing_time",
        "gemspy_total_time",
        "number_of_constraints_pypsa",
        "number_of_constraints_antares_modeler",
        "number_of_constraints_gemspy",
        "number_of_variables_pypsa",
        "number_of_variables_antares_modeler",
        "number_of_variables_gemspy",
        "pypsa_objective",
        "antares_modeler_objective_value",
        "gemspy_objective_value",
    ]
    for col in numeric_cols:
        if col in row.index:
            row[col] = pd.to_numeric(row[col], errors="coerce")

    def _n(val: Any, default: float = 0) -> float:
        """Coerce to float for display; use default if missing/invalid."""
        v = pd.to_numeric(val, errors="coerce")
        return default if pd.isna(v) else float(v)

    # Print overview statistics
    print("=" * 80)
    print(f"BENCHMARK ANALYSIS - STUDY ROW {row_number}")
    print("=" * 80)

    print("\n📊 NETWORK INFORMATION:")
    print(f"  Simulation file: {row.get('pypsa_filename', 'N/A')}")
    print(f"  Network Name: {row['pypsa_network_name']}")
    print(f"  Number of Time Steps: {int(_n(row['number_of_time_steps']))}")
    print(f"  PyPSA Version: {row['pypsa_version']}")
    print(f"  Antares Modeler Version: {row['antares_version']}")
    print(f"  GemsPy Version: {get_gemspy_version()}")

    print("\n🔧 NETWORK COMPONENTS:")
    print(f"  Buses: {int(_n(row['number_of_buses']))}")
    print(f"  Generators: {int(_n(row['number_of_generators']))}")
    print(f"  Loads: {int(_n(row['number_of_loads']))}")
    print(f"  Links: {int(_n(row['number_of_links']))}")
    print(f"  Storage Units: {int(_n(row['number_of_storage_units']))}")
    print(f"  Stores: {int(_n(row['number_of_stores']))}")
    print(f"  Lines: {int(_n(row['number_of_lines']))}")
    print(f"  Transformers: {int(_n(row['number_of_transformers']))}")
    print(f"  Shunt Impedances: {int(_n(row['number_of_shunt_impedances']))}")

    print("\n⏱️  TIMING INFORMATION:")
    print(f"  PyPSA Parsing Time (.nc load): {_n(row['parsing_time']):.4f} s")
    print(f"  PyPSA Preprocessing Time: {_n(row['preprocessing_time_pypsa_network']):.4f} s")
    print(f"  PyPSA to GEMS Conversion Time: {_n(row['pypsa_to_gems_conversion_time']):.4f} s")
    print(f"  PyPSA Build Time: {_n(row['pypsa_build_time']):.4f} s")
    print(f"  PyPSA Solve Time: {_n(row['pypsa_solve_time']):.4f} s")
    print(f"  PyPSA Total Time (build + solve): {_n(row['total_time_pypsa']):.4f} s")
    antares_modeler_parsing = _n(row.get("antares_modeler_parsing_time"))
    antares_modeler_writing = _n(row.get("antares_modeler_writing_time"))
    print(f"  Antares Modeler Parsing Time: {antares_modeler_parsing:.4f} s")
    print(f"  Antares Modeler Build Time: {_n(row.get('antares_modeler_build_time')):.4f} s")
    print(f"  Antares Modeler Solve Time: {_n(row.get('antares_modeler_solve_time')):.4f} s")
    print(f"  Antares Modeler Writing Time: {antares_modeler_writing:.4f} s")
    print(
        f"  Antares Modeler Total Time (parsing + build + solve + writing): "
        f"{_n(row['antares_modeler_total_time']):.4f} s"
    )
    gemspy_total = _n(row.get("gemspy_total_time"))
    if gemspy_total:
        print(f"  GemsPy Parsing Time: {_n(row.get('gemspy_parsing_time')):.4f} s")
        print(f"  GemsPy Build Time: {_n(row.get('gemspy_build_time')):.4f} s")
        print(f"  GemsPy Solve Time: {_n(row.get('gemspy_solve_time')):.4f} s")
        print(f"  GemsPy Writing Time: {_n(row.get('gemspy_writing_time')):.4f} s")
        print(f"  GemsPy Total Time (parsing + build + solve + writing): {gemspy_total:.4f} s")

    # PyPSA constraint/variable counts; Antares Modeler counts optional (not in Antares 9.3.7)
    has_antares_modeler_stats = (
        "number_of_constraints_antares_modeler" in row.index and "number_of_variables_antares_modeler" in row.index
    )
    n_const_pypsa = _n(row.get("number_of_constraints_pypsa"))
    n_var_pypsa = _n(row.get("number_of_variables_pypsa"))
    n_const_antares_modeler = _n(row.get("number_of_constraints_antares_modeler"))
    n_var_antares_modeler = _n(row.get("number_of_variables_antares_modeler"))
    n_const_gemspy = _n(row.get("number_of_constraints_gemspy"))
    n_var_gemspy = _n(row.get("number_of_variables_gemspy"))
    print("\n📈 OPTIMIZATION PROBLEM SIZE:")
    print(f"  PyPSA Constraints: {int(n_const_pypsa)}")
    print(f"  PyPSA Variables: {int(n_var_pypsa)}")
    if has_antares_modeler_stats:
        print(f"  Antares Modeler Constraints: {int(n_const_antares_modeler)}")
        if n_const_antares_modeler:
            print(f"  Constraints Ratio (PyPSA/Antares Modeler): {n_const_pypsa / n_const_antares_modeler:.4f}")
        else:
            print("  Constraints Ratio (PyPSA/Antares Modeler): N/A")
        print(f"  Antares Modeler Variables: {int(n_var_antares_modeler)}")
        if n_var_antares_modeler:
            print(f"  Variables Ratio (PyPSA/Antares Modeler): {n_var_pypsa / n_var_antares_modeler:.4f}")
        else:
            print("  Variables Ratio (PyPSA/Antares Modeler): N/A")
    else:
        print("  Antares Modeler constraints/variables: N/A (not reported by this Antares version)")
    if n_const_gemspy or n_var_gemspy:
        print(f"  GemsPy Constraints: {int(n_const_gemspy)}")
        print(f"  GemsPy Variables: {int(n_var_gemspy)}")

    pypsa_obj = _n(row["pypsa_objective"])
    antares_modeler_obj = _n(row["antares_modeler_objective_value"])
    gemspy_obj = _n(row.get("gemspy_objective_value"))
    print("\n🎯 OBJECTIVE VALUES:")
    print(f"  PyPSA Objective: {pypsa_obj:.6f}")
    print(f"  Antares Modeler Objective: {antares_modeler_obj:.6f}")
    obj_diff = pypsa_obj - antares_modeler_obj
    obj_diff_pct = (obj_diff / antares_modeler_obj) * 100 if antares_modeler_obj else 0.0
    print(f"  Difference (PyPSA - Antares Modeler): {obj_diff:.6f} ({obj_diff_pct:+.4f}%)")
    if gemspy_obj:
        print(f"  GemsPy Objective: {gemspy_obj:.6f}")
        gemspy_diff = pypsa_obj - gemspy_obj
        gemspy_diff_pct = (gemspy_diff / gemspy_obj) * 100 if gemspy_obj else 0.0
        print(f"  Difference (PyPSA - GemsPy): {gemspy_diff:.6f} ({gemspy_diff_pct:+.4f}%)")

    print("\n⚙️  SOLVER INFORMATION:")
    print(f"  PyPSA Solver: {row['solver_name_pypsa']} {row['solver_version_pypsa']}")
    print(f"  Antares Modeler Solver: {row['antares_modeler_solver_name']}")
    print(f"  Antares Modeler Solver Parameters: {row['antares_modeler_solver_parameters']}")
    if gemspy_obj:
        print(f"  GemsPy Solver: {row.get('gemspy_solver_name', 'N/A')}")
        print(f"  GemsPy Solver Parameters: {row.get('gemspy_solver_parameters', 'N/A')}")

    total_pypsa = _n(row["total_time_pypsa"])
    total_antares_modeler = _n(row["antares_modeler_total_time"])
    print("\n📊 PERFORMANCE COMPARISON:")
    time_ratio_antares_modeler = total_pypsa / total_antares_modeler if total_antares_modeler else float("nan")
    if pd.isna(time_ratio_antares_modeler):
        print("  Time Ratio PyPSA / Antares Modeler: N/A (missing or invalid Antares Modeler time)")
    else:
        faster = "PyPSA" if time_ratio_antares_modeler < 1 else "Antares Modeler"
        speedup = 1 / time_ratio_antares_modeler if time_ratio_antares_modeler < 1 else time_ratio_antares_modeler
        print(
            f"  Time Ratio PyPSA / Antares Modeler: {time_ratio_antares_modeler:.4f}x "
            f"({faster} is {speedup:.2f}x faster)"
        )
    if gemspy_obj and gemspy_total:
        gemspy_ratio = total_pypsa / gemspy_total
        faster = "PyPSA" if gemspy_ratio < 1 else "GemsPy"
        speedup = 1 / gemspy_ratio if gemspy_ratio < 1 else gemspy_ratio
        print(f"  Time Ratio PyPSA / GemsPy: {gemspy_ratio:.4f}x ({faster} is {speedup:.2f}x faster)")

    print("\n" + "=" * 80)

    # Create visualizations: top row (4 plots) + bottom row (3 plots + wide end-to-end)
    fig = plt.figure(figsize=(20, 12))
    from matplotlib.gridspec import GridSpec

    gs = GridSpec(2, 8, figure=fig, hspace=0.6, wspace=0.5)

    # 1. Objective Value Comparison
    ax1 = fig.add_subplot(gs[0, 0:2])
    categories = ["PyPSA", "Antares Modeler", "GemsPy"]
    objectives = [pypsa_obj, antares_modeler_obj, gemspy_obj]
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
    antares_modeler_parsing_time_plot = _n(row.get("antares_modeler_parsing_time"))
    antares_modeler_writing_time_plot = _n(row.get("antares_modeler_writing_time"))
    gemspy_parsing_time_plot = _n(row.get("gemspy_parsing_time"))
    gemspy_build = _n(row.get("gemspy_build_time"))
    gemspy_solve = _n(row.get("gemspy_solve_time"))
    gemspy_writing_time_plot = _n(row.get("gemspy_writing_time"))
    # PyPSA parsing = loading the .nc file; tracked separately, NOT included in build time
    pypsa_parsing_time_plot = _n(row.get("parsing_time"))

    # 2. Total time: parsing + build + solve (+ writing for Antares Modeler / GemsPy)
    ax2 = fig.add_subplot(gs[0, 2:4])
    pypsa_build = _n(row["pypsa_build_time"])
    pypsa_solve = _n(row["pypsa_solve_time"])
    antares_modeler_build = _n(row.get("antares_modeler_build_time"))
    antares_modeler_solve = _n(row.get("antares_modeler_solve_time"))
    if antares_modeler_build == 0 and antares_modeler_solve == 0 and total_antares_modeler > 0:
        antares_modeler_build = 0.0
        antares_modeler_solve = total_antares_modeler

    # PyPSA: parsing / build / solve — no writing (results stay in-memory)
    # Antares Modeler / GemsPy: parsing / build / solve / writing
    bottom_pypsa: float = 0.0
    bottom_antares_modeler: float = 0.0
    bottom_gemspy: float = 0.0
    layer_defs = [
        ("Parsing", pypsa_parsing_time_plot, antares_modeler_parsing_time_plot, gemspy_parsing_time_plot, "#2e86ab"),
        ("Build", pypsa_build, antares_modeler_build, gemspy_build, "steelblue"),
        ("Solve", pypsa_solve, antares_modeler_solve, gemspy_solve, "coral"),
        ("Writing", 0.0, antares_modeler_writing_time_plot, gemspy_writing_time_plot, "#f18f01"),
    ]
    bar_refs = []
    for label, pypsa_val, mod_val, gem_val, color in layer_defs:
        b = ax2.bar(
            ["PyPSA", "Antares Modeler", "GemsPy"],
            [pypsa_val, mod_val, gem_val],
            bottom=[bottom_pypsa, bottom_antares_modeler, bottom_gemspy],
            label=label,
            color=color,
            alpha=0.8,
            edgecolor="black",
        )
        bar_refs.append(b)
        bottom_pypsa += pypsa_val
        bottom_antares_modeler += mod_val
        bottom_gemspy += gem_val

    ax2.set_ylabel("Time (seconds)", fontsize=11)
    ax2.set_title("Time Comparison (Parsing + Build + Solve + Writing)", fontsize=12, fontweight="bold", pad=10)
    ax2.grid(True, alpha=0.3, axis="y")
    for i, total_h in enumerate([bottom_pypsa, bottom_antares_modeler, bottom_gemspy]):
        if total_h > 0:
            ax2.text(i, total_h, f"{total_h:.3f}s", ha="center", va="bottom", fontsize=9)
    ax2.legend(fontsize=9)

    # 3. Constraints Comparison
    ax3 = fig.add_subplot(gs[0, 4:6])
    constraints = [int(n_const_pypsa), int(n_const_antares_modeler), int(n_const_gemspy)]
    bars = ax3.bar(categories, constraints, color=["steelblue", "coral", "seagreen"], alpha=0.7, edgecolor="black")
    ax3.set_ylabel("Number of Constraints", fontsize=11)
    ax3.set_title("Constraints Comparison", fontsize=12, fontweight="bold", pad=10)
    ax3.grid(True, alpha=0.3, axis="y")
    for bar, val in zip(bars, constraints):
        height = bar.get_height()
        ax3.text(bar.get_x() + bar.get_width() / 2.0, height, f"{val:,}", ha="center", va="bottom", fontsize=9)

    # 4. Variables Comparison
    ax4 = fig.add_subplot(gs[0, 6:8])
    variables = [int(n_var_pypsa), int(n_var_antares_modeler), int(n_var_gemspy)]
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
        _n(row["pypsa_build_time"]),
        _n(row["pypsa_solve_time"]),
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
    _antares_modeler_build = _n(row.get("antares_modeler_build_time"))
    _antares_modeler_solve = _n(row.get("antares_modeler_solve_time"))
    if _antares_modeler_build == 0 and _antares_modeler_solve == 0 and total_antares_modeler > 0:
        _antares_modeler_build = 0.0
        _antares_modeler_solve = total_antares_modeler
    antares_modeler_pie_labels = ["Parsing (YAML + systems)", "Build", "Solve", "Writing sim. table"]
    antares_modeler_pie_vals = [
        antares_modeler_parsing_time_plot,
        _antares_modeler_build,
        _antares_modeler_solve,
        antares_modeler_writing_time_plot,
    ]
    antares_modeler_pie_colors = ["#2e86ab", "steelblue", "coral", "#f18f01"]
    # Only include non-zero slices
    filtered = [
        (lbl, v, c)
        for lbl, v, c in zip(antares_modeler_pie_labels, antares_modeler_pie_vals, antares_modeler_pie_colors)
        if v > 0
    ]
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

    # 8. End-to-end comparison: full PyPSA / Antares Modeler / GemsPy paths (stacked bars)
    # PyPSA:   Parsing (.nc) / Preprocessing / Build / Solve
    # Antares Modeler / GemsPy: Preprocessing / Conversion / Parsing / Build / Solve / Writing
    ax8 = fig.add_subplot(gs[1, 6:8])
    antares_modeler_build_time = _n(row.get("antares_modeler_build_time"))
    antares_modeler_solve_time = _n(row.get("antares_modeler_solve_time"))
    if antares_modeler_build_time == 0 and antares_modeler_solve_time == 0 and total_antares_modeler > 0:
        antares_modeler_solve_time = total_antares_modeler
    e2e_layer_defs = [
        # (label, pypsa_val, antares_modeler_val, gemspy_val, color)
        ("Parsing (.nc)", pypsa_parsing_time_plot, 0.0, 0.0, "#2e86ab"),
        ("Preprocessing", preproc_time, preproc_time, preproc_time, "#5c7a29"),
        ("Conversion", 0.0, conversion_time, conversion_time, "#a23b72"),
        ("Parsing (YAML/study)", 0.0, antares_modeler_parsing_time_plot, gemspy_parsing_time_plot, "#4db6d0"),
        ("Build", pypsa_build, antares_modeler_build_time, gemspy_build, "steelblue"),
        ("Solve", pypsa_solve, antares_modeler_solve_time, gemspy_solve, "coral"),
        ("Writing sim. table", 0.0, antares_modeler_writing_time_plot, gemspy_writing_time_plot, "#f18f01"),
    ]
    bot_pypsa: float = 0.0
    bot_antares_modeler: float = 0.0
    bot_gemspy: float = 0.0
    for e2e_label, e2e_pypsa, e2e_mod, e2e_gem, e2e_color in e2e_layer_defs:
        ax8.bar(
            ["PyPSA", "Antares Modeler", "GemsPy"],
            [e2e_pypsa, e2e_mod, e2e_gem],
            bottom=[bot_pypsa, bot_antares_modeler, bot_gemspy],
            label=e2e_label,
            color=e2e_color,
            alpha=0.8,
            edgecolor="black",
        )
        bot_pypsa += e2e_pypsa
        bot_antares_modeler += e2e_mod
        bot_gemspy += e2e_gem
    for i, total_h in enumerate([bot_pypsa, bot_antares_modeler, bot_gemspy]):
        if total_h > 0:
            ax8.text(i, total_h, f"{total_h:.3f}s", ha="center", va="bottom", fontsize=9)
    ax8.set_ylabel("Time (seconds)", fontsize=11)
    ax8.set_title("End-to-end Comparison", fontsize=12, fontweight="bold", pad=10)
    ax8.legend(fontsize=8, loc="upper left", bbox_to_anchor=(1.02, 1), borderaxespad=0)
    ax8.grid(True, alpha=0.3, axis="y")

    fig.suptitle(
        f"Benchmark Analysis - Study Row {row_number}: {row['pypsa_network_name']}",
        fontsize=14,
        fontweight="bold",
        y=0.995,
    )
    fig.tight_layout(rect=(0, 0, 0.97, 0.99))
    plt.show()

    return df


def get_results_path() -> Path:
    """Get the path to the benchmark results CSV file."""
    current_dir = Path().resolve()
    for parent in current_dir.parents:
        if (parent / "tmp" / "benchmark_results" / "all_studies_results.csv").exists():
            return parent / "tmp" / "benchmark_results" / "all_studies_results.csv"

    return Path("tmp") / "benchmark_results" / "all_studies_results.csv"


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
