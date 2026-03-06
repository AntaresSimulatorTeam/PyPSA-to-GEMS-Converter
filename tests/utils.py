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

import time
from pathlib import Path
from typing import Any

import matplotlib.pyplot as plt
import pandas as pd
from matplotlib.patches import Patch
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


def replace_lines_by_links(network: Network) -> Network:
    """
    Replace lines in a PyPSA network with equivalent links.

    This function converts transmission lines to links, which allows for more
    flexible modeling of power flow constraints. Each line is replaced with
    two links (one for each direction) to maintain bidirectional flow capability.
    """

    # Create a copy of the lines DataFrame to iterate over
    lines = network.lines.copy()

    # For each line, create two links (one for each direction)
    for idx, line in lines.iterrows():
        # Get line parameters
        bus0 = line["bus0"]
        bus1 = line["bus1"]
        s_nom = line["s_nom"]
        efficiency = 1.0

        # Add forward link
        network.add(
            "Link",
            f"{idx}-link-{bus0}-{bus1}",
            bus0=bus0,
            bus1=bus1,
            p_min_pu=-1,
            p_max_pu=1,
            p_nom=s_nom,  # Use line capacity as link capacity
            efficiency=efficiency,
        )
    network.remove("Line", lines.index)
    return network


def preprocess_network(network: Network, quota: bool, lines_to_links: bool) -> Network:
    if quota:
        network = extend_quota(network)
    if lines_to_links:
        network = replace_lines_by_links(network)
    return network


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
        "build_optimization_problem_time_pypsa",
        "pypsa_optimization_time",
        "total_time_pypsa",
        "modeler_parsing_time",
        "modeler_build_time",
        "modeler_solve_time",
        "modeler_writing_time",
        "modeler_total_time",
        "number_of_constraints_pypsa",
        "number_of_constraints_modeler",
        "number_of_variables_pypsa",
        "number_of_variables_modeler",
        "pypsa_objective",
        "modeler_objective_value",
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
    print(f"  Antares Version: {row['antares_version']}")

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
    print(f"  Parsing Time (PyPSA .nc load): {_n(row['parsing_time']):.4f} s")
    print(f"  Preprocessing Time (PyPSA): {_n(row['preprocessing_time_pypsa_network']):.4f} s")
    print(f"  PyPSA to GEMS Conversion Time: {_n(row['pypsa_to_gems_conversion_time']):.4f} s")
    print(f"  Build Optimization Problem Time (PyPSA): {_n(row['build_optimization_problem_time_pypsa']):.4f} s")
    print(f"  PyPSA Optimization Time: {_n(row['pypsa_optimization_time']):.4f} s")
    print(f"  PyPSA Total Time: {_n(row['total_time_pypsa']):.4f} s")
    modeler_parsing = _n(row.get("modeler_parsing_time"))
    modeler_writing = _n(row.get("modeler_writing_time"))
    print(f"  Modeler Parsing Time (YAML load): {modeler_parsing:.4f} s")
    print(f"  Modeler Build Time: {_n(row.get('modeler_build_time')):.4f} s")
    print(f"  Modeler Solve Time: {_n(row.get('modeler_solve_time')):.4f} s")
    print(f"  Modeler Writing Time (simulation table): {modeler_writing:.4f} s")
    # modeler_total_time in CSV = parsing + build + solve + writing (Antares binary)
    print(f"  Modeler (parsing+build+solve+writing) Time: {_n(row['modeler_total_time']):.4f} s")
    preproc = _n(row.get("preprocessing_time_pypsa_network"))
    conversion = _n(row.get("pypsa_to_gems_conversion_time"))
    full_modeler_path = preproc + conversion + _n(row["modeler_total_time"])
    print(f"  Full Modeler Path (preproc + conversion + parsing + build + solve + writing): {full_modeler_path:.4f} s")

    # PyPSA constraint/variable counts; modeler counts optional (not in Antares 9.3.7)
    has_modeler_stats = "number_of_constraints_modeler" in row.index and "number_of_variables_modeler" in row.index
    n_const_pypsa = _n(row.get("number_of_constraints_pypsa"))
    n_var_pypsa = _n(row.get("number_of_variables_pypsa"))
    print("\n📈 OPTIMIZATION PROBLEM SIZE:")
    print(f"  PyPSA Constraints: {int(n_const_pypsa)}")
    print(f"  PyPSA Variables: {int(n_var_pypsa)}")
    if has_modeler_stats:
        n_const_modeler = _n(row.get("number_of_constraints_modeler"))
        n_var_modeler = _n(row.get("number_of_variables_modeler"))
        print(f"  Modeler Constraints: {int(n_const_modeler)}")
        if n_const_modeler:
            print(f"  Constraints Ratio (PyPSA/Modeler): {n_const_pypsa / n_const_modeler:.4f}")
        else:
            print("  Constraints Ratio (PyPSA/Modeler): N/A")
        print(f"  Modeler Variables: {int(n_var_modeler)}")
        if n_var_modeler:
            print(f"  Variables Ratio (PyPSA/Modeler): {n_var_pypsa / n_var_modeler:.4f}")
        else:
            print("  Variables Ratio (PyPSA/Modeler): N/A")
    else:
        n_const_modeler = 0
        n_var_modeler = 0
        print("  Modeler constraints/variables: N/A (not reported by this Antares version)")

    pypsa_obj = _n(row["pypsa_objective"])
    modeler_obj = _n(row["modeler_objective_value"])
    print("\n🎯 OBJECTIVE VALUES:")
    print(f"  PyPSA Objective: {pypsa_obj:.6f}")
    print(f"  Modeler Objective: {modeler_obj:.6f}")
    obj_diff = pypsa_obj - modeler_obj
    obj_diff_pct = (obj_diff / modeler_obj) * 100 if modeler_obj else 0.0
    print(f"  Difference: {obj_diff:.6f} ({obj_diff_pct:+.4f}%)")

    print("\n⚙️  SOLVER INFORMATION:")
    print(f"  PyPSA Solver: {row['solver_name_pypsa']} {row['solver_version_pypsa']}")
    print(f"  Modeler Solver: {row['modeler_solver_name']}")
    print(f"  Modeler Solver Parameters: {row['modeler_solver_parameters']}")

    total_pypsa = _n(row["total_time_pypsa"])
    total_modeler = _n(row["modeler_total_time"])  # parsing+build+solve+writing (Antares binary)
    full_modeler_path = preproc + conversion + total_modeler
    print("\n📊 PERFORMANCE COMPARISON:")
    time_ratio_binary = total_pypsa / total_modeler if total_modeler else float("nan")
    print(f"  Time Ratio PyPSA / Modeler (parsing+build+solve+writing): {time_ratio_binary:.4f}x")
    time_ratio_full = total_pypsa / full_modeler_path if full_modeler_path else float("nan")
    print(f"  Time Ratio PyPSA / Full modeler path: {time_ratio_full:.4f}x")
    if pd.notna(time_ratio_binary) and time_ratio_binary > 0:
        if time_ratio_binary < 1:
            print(f"  → PyPSA is {1 / time_ratio_binary:.2f}x faster (vs modeler binary)")
        else:
            print(f"  → Modeler binary is {time_ratio_binary:.2f}x faster")
    if pd.notna(time_ratio_full) and time_ratio_full > 0:
        if time_ratio_full < 1:
            print(f"  → PyPSA is {1 / time_ratio_full:.2f}x faster (vs full modeler path)")
        else:
            print(f"  → Full modeler path is {time_ratio_full:.2f}x faster")
    if pd.isna(time_ratio_binary) and pd.isna(time_ratio_full):
        print("  → N/A (missing or invalid times)")

    print("\n" + "=" * 80)

    # Create visualizations: top row (4 plots) + bottom row (3 plots + wide end-to-end)
    fig = plt.figure(figsize=(20, 12))
    from matplotlib.gridspec import GridSpec

    gs = GridSpec(2, 8, figure=fig, hspace=0.6, wspace=0.5)

    # 1. Objective Value Comparison
    ax1 = fig.add_subplot(gs[0, 0:2])
    categories = ["PyPSA", "Modeler"]
    objectives = [pypsa_obj, modeler_obj]
    bars = ax1.bar(categories, objectives, color=["steelblue", "coral"], alpha=0.7, edgecolor="black")
    ax1.set_ylabel("Objective Value", fontsize=11)
    ax1.set_title("Objective Value Comparison", fontsize=12, fontweight="bold", pad=10)
    ax1.grid(True, alpha=0.3, axis="y")
    # Add value labels on bars
    for bar, val in zip(bars, objectives):
        height = bar.get_height()
        ax1.text(bar.get_x() + bar.get_width() / 2.0, height, f"{val:.2e}", ha="center", va="bottom", fontsize=9)

    # Used in ax2, ax6 (modeler pie), ax7 (modeler pipeline stack)
    preproc_time = _n(row.get("preprocessing_time_pypsa_network"))
    conversion_time = _n(row.get("pypsa_to_gems_conversion_time"))
    modeler_parsing_time_plot = _n(row.get("modeler_parsing_time"))
    modeler_writing_time_plot = _n(row.get("modeler_writing_time"))
    # PyPSA parsing = loading the .nc file; tracked separately, NOT included in build time
    pypsa_parsing_time_plot = _n(row.get("parsing_time"))

    # 2. Total time: parsing+build+solve for PyPSA (no writing); parsing+build+solve+writing for Modeler
    ax2 = fig.add_subplot(gs[0, 2:4])
    pypsa_build = _n(row["build_optimization_problem_time_pypsa"])
    pypsa_solve = _n(row["pypsa_optimization_time"])
    modeler_build = _n(row.get("modeler_build_time"))
    modeler_solve = _n(row.get("modeler_solve_time"))
    if modeler_build == 0 and modeler_solve == 0 and total_modeler > 0:
        modeler_build = 0.0
        modeler_solve = total_modeler

    # PyPSA: parsing (.nc load) / build / solve — no writing (results stay in-memory)
    # Modeler: parsing (YAML load) / build / solve / writing (simulation table to disk)
    bottom_pypsa: float = 0.0
    bottom_modeler: float = 0.0
    layer_defs = [
        ("Parsing", pypsa_parsing_time_plot, modeler_parsing_time_plot, "#2e86ab"),
        ("Build", pypsa_build, modeler_build, "steelblue"),
        ("Solve", pypsa_solve, modeler_solve, "coral"),
        ("Writing", 0.0, modeler_writing_time_plot, "#f18f01"),
    ]
    bar_refs = []
    for label, pypsa_val, mod_val, color in layer_defs:
        b = ax2.bar(
            ["PyPSA", "Modeler"],
            [pypsa_val, mod_val],
            bottom=[bottom_pypsa, bottom_modeler],
            label=label,
            color=color,
            alpha=0.8,
            edgecolor="black",
        )
        bar_refs.append(b)
        bottom_pypsa += pypsa_val
        bottom_modeler += mod_val

    ax2.set_ylabel("Time (seconds)", fontsize=11)
    ax2.set_title("Time Comparison (Build + Solve)", fontsize=12, fontweight="bold", pad=10)
    ax2.grid(True, alpha=0.3, axis="y")
    for i, total_h in enumerate([bottom_pypsa, bottom_modeler]):
        if total_h > 0:
            ax2.text(i, total_h, f"{total_h:.3f}s", ha="center", va="bottom", fontsize=9)
    ax2.legend(fontsize=9)

    # 3. Constraints Comparison
    ax3 = fig.add_subplot(gs[0, 4:6])
    constraints = [int(n_const_pypsa), int(n_const_modeler)]
    bars = ax3.bar(categories, constraints, color=["steelblue", "coral"], alpha=0.7, edgecolor="black")
    ax3.set_ylabel("Number of Constraints", fontsize=11)
    ax3.set_title("Constraints Comparison", fontsize=12, fontweight="bold", pad=10)
    ax3.grid(True, alpha=0.3, axis="y")
    for bar, val in zip(bars, constraints):
        height = bar.get_height()
        ax3.text(bar.get_x() + bar.get_width() / 2.0, height, f"{val:,}", ha="center", va="bottom", fontsize=9)

    # 4. Variables Comparison
    ax4 = fig.add_subplot(gs[0, 6:8])
    variables = [int(n_var_pypsa), int(n_var_modeler)]
    bars = ax4.bar(categories, variables, color=["steelblue", "coral"], alpha=0.7, edgecolor="black")
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

    # 6. Modeler Time Breakdown (parsing / build / solve / writing)
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
    ax6.set_title("Modeler Time Breakdown (binary)", fontsize=12, fontweight="bold", pad=10)

    # 7. Modeler only: full pipeline stack (preprocessing / conversion / parsing / build / solve / writing)
    ax7 = fig.add_subplot(gs[1, 4:6])
    modeler_build_time = _n(row.get("modeler_build_time"))
    modeler_solve_time = _n(row.get("modeler_solve_time"))
    if modeler_build_time == 0 and modeler_solve_time == 0 and total_modeler > 0:
        modeler_build_time = 0.0
        modeler_solve_time = total_modeler
    modeler_stages = ["Preprocessing", "Conversion", "Parsing", "Build", "Solve", "Writing sim. table"]
    modeler_stage_times = [
        preproc_time,
        conversion_time,
        modeler_parsing_time_plot,
        modeler_build_time,
        modeler_solve_time,
        modeler_writing_time_plot,
    ]
    # Distinct colors for modeler pipeline stages
    colors_stack = ["#2e86ab", "#a23b72", "#5c7a29", "#f18f01", "#c73e1d", "#6b4c9a"]
    left: float = 0.0
    total_pipeline = sum(modeler_stage_times)
    legend_handles = []
    legend_labels = []

    for i, (stage, t) in enumerate(zip(modeler_stages, modeler_stage_times)):
        if t > 0:
            bar = ax7.bar(
                0,
                t,
                width=0.5,
                bottom=left,
                color=colors_stack[i],
                alpha=0.85,
                edgecolor="black",
            )
            legend_handles.append(bar.patches[0])
            left += t
        else:
            legend_handles.append(Patch(facecolor=colors_stack[i], alpha=0.85, edgecolor="black"))
        pct = f" ({100 * t / total_pipeline:.1f}%)" if total_pipeline else ""
        legend_labels.append(f"{stage}: {t:.3f}s{pct}")
    ax7.set_xlim(-0.5, 0.5)
    ax7.set_xticks([0])
    ax7.set_xticklabels(["Modeler"])
    ax7.set_ylabel("Time (seconds)", fontsize=11)
    if total_pipeline > 0:
        ax7.text(0, total_pipeline, f"{total_pipeline:.3f}s", ha="center", va="bottom", fontsize=9)
    ax7.set_title("Modeler Full Pipeline", fontsize=12, fontweight="bold", pad=10)
    ax7.legend(legend_handles, legend_labels, title="Stage", fontsize=9)
    ax7.grid(True, alpha=0.3, axis="y")

    # 8. End-to-end comparison: full PyPSA path vs full Modeler path (side-by-side stacked bars)
    # PyPSA:   Parsing (.nc) / Preprocessing / Build / Solve
    # Modeler: Preprocessing / Conversion / Parsing (YAML) / Build / Solve / Writing sim. table
    ax8 = fig.add_subplot(gs[1, 6:8])
    e2e_layer_defs = [
        # (label,               pypsa_val,              modeler_val,                   color)
        ("Parsing (.nc)", pypsa_parsing_time_plot, 0.0, "#2e86ab"),
        ("Preprocessing", preproc_time, preproc_time, "#5c7a29"),
        ("Conversion", 0.0, conversion_time, "#a23b72"),
        ("Parsing (YAML)", 0.0, modeler_parsing_time_plot, "#4db6d0"),
        ("Build", pypsa_build, modeler_build_time, "steelblue"),
        ("Solve", pypsa_solve, modeler_solve_time, "coral"),
        ("Writing sim. table", 0.0, modeler_writing_time_plot, "#f18f01"),
    ]
    bot_pypsa: float = 0.0
    bot_modeler: float = 0.0
    for e2e_label, e2e_pypsa, e2e_mod, e2e_color in e2e_layer_defs:
        ax8.bar(
            ["PyPSA", "Modeler"],
            [e2e_pypsa, e2e_mod],
            bottom=[bot_pypsa, bot_modeler],
            label=e2e_label,
            color=e2e_color,
            alpha=0.8,
            edgecolor="black",
        )
        bot_pypsa += e2e_pypsa
        bot_modeler += e2e_mod
    for i, total_h in enumerate([bot_pypsa, bot_modeler]):
        if total_h > 0:
            ax8.text(i, total_h, f"{total_h:.3f}s", ha="center", va="bottom", fontsize=9)
    ax8.set_ylabel("Time (seconds)", fontsize=11)
    ax8.set_title("End-to-end Comparison (Full Path)", fontsize=12, fontweight="bold", pad=10)
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
