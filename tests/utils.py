# Copyright (c) 2025, RTE (https://www.rte-france.com)
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

import matplotlib.pyplot as plt
import pandas as pd
from pypsa import Network


def load_pypsa_study(file: str, load_scaling: float) -> Network:
    """
    Load a PyPSA study from a NetCDF file, preparing it for analysis or manipulation.
    """
    current_dir = Path(__file__).resolve().parents[1]

    input_file = current_dir / "resources" / "test_files" / file

    network = Network(input_file)

    # Scale the load to make the test case feasible
    network = scale_load(network, load_scaling)

    return network


def load_pypsa_study_benchmark(file: str, load_scaling: float) -> tuple[Network, float]:
    """
    Load a PyPSA study from a NetCDF file, preparing it for analysis or manipulation.
    """
    current_dir = Path(__file__).resolve().parents[1]

    input_file = current_dir / "resources" / "test_files" / file

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
    if len(network.global_constraints) > 0 and "constant" in network.global_constraints and network.global_constraints["constant"] > 0:
        print("We have a global constraint")
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

    # Print overview statistics
    print("=" * 80)
    print(f"BENCHMARK ANALYSIS - STUDY ROW {row_number}")
    print("=" * 80)

    print("\n📊 NETWORK INFORMATION:")
    print(f"  Network Name: {row['pypsa_network_name']}")
    print(f"  Number of Time Steps: {int(row['number_of_time_steps'])}")
    print(f"  PyPSA Version: {row['pypsa_version']}")
    print(f"  Antares Version: {row['antares_version']}")

    print("\n🔧 NETWORK COMPONENTS:")
    print(f"  Buses: {int(row['number_of_buses'])}")
    print(f"  Generators: {int(row['number_of_generators'])}")
    print(f"  Loads: {int(row['number_of_loads'])}")
    print(f"  Links: {int(row['number_of_links'])}")
    print(f"  Storage Units: {int(row['number_of_storage_units'])}")
    print(f"  Stores: {int(row['number_of_stores'])}")
    print(f"  Lines: {int(row['number_of_lines'])}")
    print(f"  Transformers: {int(row['number_of_transformers'])}")
    print(f"  Shunt Impedances: {int(row['number_of_shunt_impedances'])}")

    print("\n⏱️  TIMING INFORMATION:")
    print(f"  Parsing Time: {row['parsing_time']:.4f} s")
    print(f"  Preprocessing Time (PyPSA): {row['preprocessing_time_pypsa_network']:.4f} s")
    print(f"  PyPSA to GEMS Conversion Time: {row['pypsa_to_gems_conversion_time']:.4f} s")
    print(f"  Build Optimization Problem Time (PyPSA): {row['build_optimization_problem_time_pypsa']:.4f} s")
    print(f"  PyPSA Optimization Time: {row['pypsa_optimization_time']:.4f} s")
    print(f"  PyPSA Total Time: {row['total_time_pypsa']:.4f} s")
    print(f"  Modeler Total Time: {row['modeler_total_time']:.4f} s")

    print("\n📈 OPTIMIZATION PROBLEM SIZE:")
    print(f"  PyPSA Constraints: {int(row['number_of_constraints_pypsa'])}")
    print(f"  Modeler Constraints: {int(row['number_of_constraints_modeler'])}")
    print(
        f"  Constraints Ratio (PyPSA/Modeler): {row['number_of_constraints_pypsa'] / row['number_of_constraints_modeler']:.4f}"
    )
    print(f"  PyPSA Variables: {int(row['number_of_variables_pypsa'])}")
    print(f"  Modeler Variables: {int(row['number_of_variables_modeler'])}")
    print(
        f"  Variables Ratio (PyPSA/Modeler): {row['number_of_variables_pypsa'] / row['number_of_variables_modeler']:.4f}"
    )

    print("\n🎯 OBJECTIVE VALUES:")
    print(f"  PyPSA Objective: {row['pypsa_objective']:.6f}")
    print(f"  Modeler Objective: {row['modeler_objective_value']:.6f}")
    obj_diff = row["pypsa_objective"] - row["modeler_objective_value"]
    obj_diff_pct = (obj_diff / row["modeler_objective_value"]) * 100
    print(f"  Difference: {obj_diff:.6f} ({obj_diff_pct:+.4f}%)")

    print("\n⚙️  SOLVER INFORMATION:")
    print(f"  PyPSA Solver: {row['solver_name_pypsa']} {row['solver_version_pypsa']}")
    print(f"  Modeler Solver: {row['modeler_solver_name']}")
    print(f"  Modeler Solver Parameters: {row['modeler_solver_parameters']}")

    print("\n📊 PERFORMANCE COMPARISON:")
    time_ratio = row["total_time_pypsa"] / row["modeler_total_time"]
    print(f"  Time Ratio (PyPSA/Modeler): {time_ratio:.4f}x")
    if time_ratio < 1:
        print(f"  → PyPSA is {1 / time_ratio:.2f}x faster")
    else:
        print(f"  → Modeler is {time_ratio:.2f}x faster")

    print("\n" + "=" * 80)

    # Create visualizations
    plt.figure(figsize=(16, 12))

    # 1. Objective Value Comparison
    ax1 = plt.subplot(2, 3, 1)
    categories = ["PyPSA", "Modeler"]
    objectives = [row["pypsa_objective"], row["modeler_objective_value"]]
    bars = ax1.bar(categories, objectives, color=["steelblue", "coral"], alpha=0.7, edgecolor="black")
    ax1.set_ylabel("Objective Value", fontsize=11)
    ax1.set_title("Objective Value Comparison", fontsize=12, fontweight="bold")
    ax1.grid(True, alpha=0.3, axis="y")
    # Add value labels on bars
    for bar, val in zip(bars, objectives):
        height = bar.get_height()
        ax1.text(bar.get_x() + bar.get_width() / 2.0, height, f"{val:.2e}", ha="center", va="bottom", fontsize=9)

    # 2. Time Comparison
    ax2 = plt.subplot(2, 3, 2)
    times = [row["total_time_pypsa"], row["modeler_total_time"]]
    bars = ax2.bar(categories, times, color=["steelblue", "coral"], alpha=0.7, edgecolor="black")
    ax2.set_ylabel("Time (seconds)", fontsize=11)
    ax2.set_title("Total Time Comparison", fontsize=12, fontweight="bold")
    ax2.grid(True, alpha=0.3, axis="y")
    for bar, val in zip(bars, times):
        height = bar.get_height()
        ax2.text(bar.get_x() + bar.get_width() / 2.0, height, f"{val:.3f}s", ha="center", va="bottom", fontsize=9)

    # 3. Constraints Comparison
    ax3 = plt.subplot(2, 3, 3)
    constraints = [int(row["number_of_constraints_pypsa"]), int(row["number_of_constraints_modeler"])]
    bars = ax3.bar(categories, constraints, color=["steelblue", "coral"], alpha=0.7, edgecolor="black")
    ax3.set_ylabel("Number of Constraints", fontsize=11)
    ax3.set_title("Constraints Comparison", fontsize=12, fontweight="bold")
    ax3.grid(True, alpha=0.3, axis="y")
    for bar, val in zip(bars, constraints):
        height = bar.get_height()
        ax3.text(bar.get_x() + bar.get_width() / 2.0, height, f"{val:,}", ha="center", va="bottom", fontsize=9)

    # 4. Variables Comparison
    ax4 = plt.subplot(2, 3, 4)
    variables = [int(row["number_of_variables_pypsa"]), int(row["number_of_variables_modeler"])]
    bars = ax4.bar(categories, variables, color=["steelblue", "coral"], alpha=0.7, edgecolor="black")
    ax4.set_ylabel("Number of Variables", fontsize=11)
    ax4.set_title("Variables Comparison", fontsize=12, fontweight="bold")
    ax4.grid(True, alpha=0.3, axis="y")
    for bar, val in zip(bars, variables):
        height = bar.get_height()
        ax4.text(bar.get_x() + bar.get_width() / 2.0, height, f"{val:,}", ha="center", va="bottom", fontsize=9)

    # 5. Time Breakdown (PyPSA)
    ax5 = plt.subplot(2, 3, 5)
    pypsa_times = {
        "Preprocessing": row["preprocessing_time_pypsa_network"],
        "Conversion": row["pypsa_to_gems_conversion_time"],
        "Build Model": row["build_optimization_problem_time_pypsa"],
        "Optimization": row["pypsa_optimization_time"],
    }
    ax5.pie(
        list(pypsa_times.values()),
        labels=list(pypsa_times.keys()),
        autopct="%1.1f%%",
        startangle=90,
        colors=plt.cm.Set3.colors,
    )
    ax5.set_title("PyPSA Time Breakdown", fontsize=12, fontweight="bold")

    # 6. Objective Difference
    ax6 = plt.subplot(2, 3, 6)
    diff_pct = obj_diff_pct
    colors_bar = ["green" if abs(diff_pct) < 0.01 else "orange" if abs(diff_pct) < 1 else "red"]
    bars = ax6.bar(["Objective\nDifference"], [diff_pct], color=colors_bar, alpha=0.7, edgecolor="black")
    ax6.axhline(y=0, color="black", linestyle="-", linewidth=1)
    ax6.set_ylabel("Relative Difference (%)", fontsize=11)
    ax6.set_title("Objective Difference\n(PyPSA - Modeler) / Modeler × 100%", fontsize=12, fontweight="bold")
    ax6.grid(True, alpha=0.3, axis="y")
    for bar, val in zip(bars, [diff_pct]):
        height = bar.get_height()
        ax6.text(
            bar.get_x() + bar.get_width() / 2.0,
            height,
            f"{val:+.4f}%",
            ha="center",
            va="bottom" if val >= 0 else "top",
            fontsize=10,
            fontweight="bold",
        )

    plt.suptitle(
        f"Benchmark Analysis - Study Row {row_number}: {row['pypsa_network_name']}",
        fontsize=14,
        fontweight="bold",
        y=0.995,
    )
    plt.tight_layout(rect=(0, 0, 1, 0.99))
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
