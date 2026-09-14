"""Benchmark Antares-Xpansion (Benders decomposition) against PyPSA's monolithic solve
on an arbitrary PyPSA .nc network file, and append one row to the same results CSV
schema tests/local_benchmark/xpansion_benchmark.py uses -- so
tests/local_benchmark/xpansion_benchmark_analysis.ipynb can analyze/plot it directly
(that notebook's own comment already anticipates "the cloud setup script" as a second
producer of this CSV, alongside the pytest benchmark).

Reuses everything established getting a real stressful-weather 2-scenario network to
convert and solve cleanly (see convert_nc_to_gems.py in this same directory):
  - strip solver-output data so a previously-solved .nc converts as fresh/unsolved
  - drop unsupported global constraints (only co2_emissions/primary_energy supported)
  - homogenize static (non-time-varying) parameters across scenarios (the GEMS model
    library requires these scenario-invariant; real per-scenario floating-point noise,
    e.g. offshore wind capital_cost, otherwise makes the Antares Modeler reject the study)
  - validate horizon is a whole number of Antares weeks (n_timesteps % 168 == 0)
  - re-emit every MPS file through HiGHS before Benders solves it (tests/e2e/
    test_hybrid_study_comparison.py's proven fix for buses with no Line/Transformer
    connection -- mostly battery/H2 storage bookkeeping buses in real PyPSA-Eur
    networks -- whose theta variable Antares' own MPS writer only puts in BOUNDS,
    never COLUMNS, which Coin/Clp rejects outright; applied regardless of solver
    since it's solver-agnostic and doesn't require touching the GEMS model or the
    network's real components)

Usage:
  uv run python tests/local_benchmark/benchmark_pypsa_vs_xpansion.py --input NETWORK.nc \\
      --gems-solver-name xpress --xpress-license-path /path/to/xpauth.xpr
  uv run python tests/local_benchmark/benchmark_pypsa_vs_xpansion.py --input NETWORK.nc \\
      --gems-solver-name coin --pypsa-solver-name cbc
"""

import argparse
import logging
import shutil
import subprocess
import sys
import time
from pathlib import Path

import highspy
import pandas as pd
import pypsa

PROJECT_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(PROJECT_ROOT))

from convert_nc_to_gems import (  # noqa: E402
    drop_unsupported_global_constraints,
    get_scenario_varying_static_params,
    homogenize_static_across_scenarios,
    strip_outputs,
    validate_hybrid_study_horizon,
)
from src.dependencies import (  # noqa: E402
    get_antares_dir_name,
    get_antares_xpansion_dir_name,
    get_antares_xpansion_launcher_bin,
    get_antares_xpansion_version,
)
from src.pypsa_converter import PyPSAStudyConverter  # noqa: E402
from src.utils import (  # noqa: E402
    _latest_simulation_dir,
    _parse_xpansion_settings_ini,
    _read_yearly_weights,
    _write_benders_options,
    _XPANSION_SOLVER_TO_BENDERS,
    read_xpansion_mps_sizes,
    read_xpansion_out_json,
    write_benders_lp_weights,
)
from tests.utils import get_gemspy_version  # noqa: E402
from xpress_license import activate_and_verify  # noqa: E402

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger("benchmark_pypsa_vs_xpansion")

DEFAULT_RESULTS_CSV = PROJECT_ROOT / "tmp" / "xpansion_benchmark_results" / "xpansion_scenario_results.csv"


# --- MPS fix (see module docstring) ---


def _rewrite_mps_for_coin(src: Path, dst: Path) -> None:
    highs = highspy.Highs()
    highs.setOptionValue("output_flag", False)
    read_status = highs.readModel(str(src))
    if read_status not in (highspy.HighsStatus.kOk, highspy.HighsStatus.kWarning):
        raise RuntimeError(f"HiGHS failed to read {src}: {read_status}")
    write_status = highs.writeModel(str(dst))
    if not dst.exists():
        raise RuntimeError(f"HiGHS failed to write {dst}: {write_status}")


def run_xpansion_launcher_with_highs_fix(study_root: Path, launcher_bin: Path) -> subprocess.CompletedProcess[str]:
    install_bin = launcher_bin.parent / "bin"
    problem_generator = install_bin / "antares-problem-generator"
    benders = install_bin / "benders"

    locker = study_root / ".xpansion_locker"
    if locker.exists():
        locker.unlink()

    logger.info("Running antares-problem-generator on %s", study_root)
    generated = subprocess.run(
        [str(problem_generator), str(study_root.resolve())],
        capture_output=True,
        text=True,
        check=False,
        cwd=str(install_bin),
    )
    if generated.returncode != 0:
        logger.error("antares-problem-generator failed (returncode=%s)", generated.returncode)
        return generated

    simulation_dir = _latest_simulation_dir(study_root)
    lp_dir = simulation_dir / "lp"
    if lp_dir.exists():
        shutil.rmtree(lp_dir)
    lp_dir.mkdir()
    for src in simulation_dir.iterdir():
        if src.suffix == ".mps":
            _rewrite_mps_for_coin(src, lp_dir / src.name)
        elif src.name == "structure.txt":
            shutil.copy(src, lp_dir / src.name)
    (lp_dir / "area.txt").touch()

    settings = _parse_xpansion_settings_ini(study_root)
    yearly_weights = _read_yearly_weights(study_root)
    if yearly_weights:
        write_benders_lp_weights(lp_dir, yearly_weights)
        slave_weight, slave_weight_value = "weights.txt", float(len(yearly_weights))
    else:
        n_subproblems = sum(1 for path in lp_dir.glob("*.mps") if path.name.startswith("problem-"))
        slave_weight, slave_weight_value = "CONSTANT", float(n_subproblems) if n_subproblems else 1.0

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

    logger.info("Running benders (solver=%s) in %s", solver_name, lp_dir)
    solved = subprocess.run(
        [str(benders), "options.json"], capture_output=True, text=True, check=False, cwd=str(lp_dir)
    )
    solved.stdout = (generated.stdout or "") + (solved.stdout or "")
    solved.stderr = (generated.stderr or "") + (solved.stderr or "")
    return solved


# --- Network preparation (see convert_nc_to_gems.py for the reused pieces) ---


def load_and_prepare(input_path: Path) -> pypsa.Network:
    logger.info("Loading %s", input_path)
    n = pypsa.Network(str(input_path))
    logger.info(
        "Loaded: %d buses, %d generators, %d lines, %d links, %d storage_units, %d stores, %d loads, "
        "%d snapshots, has_scenarios=%s%s",
        len(n.buses),
        len(n.generators),
        len(n.lines),
        len(n.links),
        len(n.storage_units),
        len(n.stores),
        len(n.loads),
        len(n.snapshots),
        n.has_scenarios,
        f" ({len(n.scenarios)} scenarios)" if n.has_scenarios else "",
    )
    strip_outputs(n)
    drop_unsupported_global_constraints(n)
    homogenize_static_across_scenarios(n, get_scenario_varying_static_params())
    validate_hybrid_study_horizon(n)
    n.consistency_check()
    logger.info("Network prepared and consistency_check passed")
    return n


def run_benchmark(
    input_path: Path,
    study_dir: Path,
    *,
    gems_solver_name: str,
    pypsa_solver_name: str,
    results_csv: Path,
    keep_study: bool,
    skip_pypsa_solve: bool,
) -> dict[str, object]:
    row: dict[str, object] = {}
    row["study_name"] = input_path.stem
    row["antares_xpansion_version"] = f"v{get_antares_xpansion_version()}"
    row["gemspy_version"] = get_gemspy_version()

    # ==================================================================================
    # Load + prepare network
    # ==================================================================================
    build_start = time.time()
    network = load_and_prepare(input_path)
    network.name = network.name or input_path.stem
    row["network_build_time"] = time.time() - build_start
    row["pypsa_network_name"] = network.name
    row["n_buses"] = len(network.buses.index.get_level_values(-1).unique())
    row["n_scenarios"] = len(network.scenarios) if network.has_scenarios else 1
    row["number_of_time_steps"] = len(network.snapshots)

    # ==================================================================================
    # Converter: PyPSA -> GEMS study (Xpansion path via extendable generators)
    # ==================================================================================
    if study_dir.exists():
        shutil.rmtree(study_dir)
    start_time_conversion = time.time()
    logger.info("Converting PyPSA network to GEMS study (solver_name=%s)", gems_solver_name)
    PyPSAStudyConverter(
        pypsa_network=network, study_dir=study_dir, series_file_format=".tsv", solver_name=gems_solver_name
    ).to_gems_study()
    row["pypsa_to_gems_conversion_time"] = time.time() - start_time_conversion

    study_root = study_dir / network.name

    # ==================================================================================
    # Antares-Xpansion: antares-problem-generator + HiGHS MPS re-emit + benders
    # ==================================================================================
    launcher_bin = get_antares_xpansion_launcher_bin(PROJECT_ROOT)
    logger.info("Running Antares-Xpansion on %s", study_root)
    xpansion_start = time.time()
    result = run_xpansion_launcher_with_highs_fix(study_root, launcher_bin)
    row["xpansion_total_time"] = time.time() - xpansion_start

    if result.returncode != 0:
        row["xpansion_status"] = "FAILED"
        logger.error(
            "Xpansion FAILED (returncode=%s):\n--- stdout (tail) ---\n%s\n--- stderr (tail) ---\n%s",
            result.returncode,
            result.stdout[-4000:],
            result.stderr[-2000:],
        )
    else:
        xpansion_solution = read_xpansion_out_json(study_root)["solution"]
        row["xpansion_status"] = xpansion_solution["problem_status"]
        row["xpansion_objective_value"] = xpansion_solution["overall_cost"]

    try:
        mps_sizes = read_xpansion_mps_sizes(study_root)
        row.update(mps_sizes)
        logger.info(
            "Xpansion MPS sizes: %s vars / %s cons (master %s/%s, %s x one-sub %s/%s)",
            mps_sizes["number_of_variables_xpansion"],
            mps_sizes["number_of_constraints_xpansion"],
            mps_sizes["number_of_variables_xpansion_master"],
            mps_sizes["number_of_constraints_xpansion_master"],
            mps_sizes["number_of_xpansion_subproblems"],
            mps_sizes["number_of_variables_xpansion_subproblem"],
            mps_sizes["number_of_constraints_xpansion_subproblem"],
        )
    except (FileNotFoundError, ValueError) as exc:
        logger.warning("Could not read Xpansion MPS sizes: %s", exc)

    # ==================================================================================
    # PyPSA: build and solve the same investment problem as one monolithic LP
    # ==================================================================================
    if not skip_pypsa_solve:
        logger.info("Building PyPSA optimization problem")
        start_time_build = time.time()
        network.optimize.create_model()
        row["pypsa_build_time"] = time.time() - start_time_build

        logger.info("Solving PyPSA optimization problem (solver_name=%s)", pypsa_solver_name)
        start_time_solve = time.time()
        network.optimize.solve_model(solver_name=pypsa_solver_name)
        row["pypsa_solve_time"] = time.time() - start_time_solve
        row["pypsa_total_time"] = row["pypsa_build_time"] + row["pypsa_solve_time"]

        row["number_of_constraints_pypsa"] = network.model.ncons
        row["number_of_variables_pypsa"] = network.model.nvars
        assert network.objective is not None
        assert network.objective_constant is not None
        row["pypsa_objective"] = network.objective + network.objective_constant
        row["pypsa_solver_name"] = network.model.solver_name
    else:
        logger.info("--skip-pypsa-solve set: not solving the monolithic PyPSA problem")

    # ==================================================================================
    # Results: append one row to the combined benchmark CSV
    # ==================================================================================
    results_csv.parent.mkdir(parents=True, exist_ok=True)
    benchmark_data_frame = pd.DataFrame([row])
    file_exists = results_csv.exists()
    benchmark_data_frame.to_csv(results_csv, mode="a", header=not file_exists, index=False)
    logger.info("Appended benchmark results to %s", results_csv)

    if not keep_study:
        shutil.rmtree(study_dir, ignore_errors=True)

    return row


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--input", type=Path, required=True, help="Path to the source PyPSA .nc file")
    parser.add_argument(
        "--study-dir", type=Path, default=None, help="Where to write the converted GEMS study (default: tmp/<name>)"
    )
    parser.add_argument("--gems-solver-name", default="xpress", choices=["coin", "xpress"])
    parser.add_argument(
        "--pypsa-solver-name",
        default=None,
        help="linopy solver name for the PyPSA-side monolithic solve (default: same family as --gems-solver-name: "
        "'xpress' for xpress, 'cbc' for coin)",
    )
    parser.add_argument("--results-csv", type=Path, default=DEFAULT_RESULTS_CSV)
    parser.add_argument(
        "--xpress-license-path",
        type=Path,
        default=None,
        help="Path to a real xpauth.xpr license file. Required if either solver is 'xpress' -- "
        "without it, xpress silently runs under the free Community edition (small size cap) "
        "and a real network's solve fails deep into the run instead of failing clearly up front.",
    )
    parser.add_argument("--keep-study", action="store_true", help="Don't delete the converted GEMS study afterwards")
    parser.add_argument(
        "--skip-pypsa-solve", action="store_true", help="Only run the Xpansion side, skip the PyPSA monolithic solve"
    )
    args = parser.parse_args()

    if not (PROJECT_ROOT / get_antares_dir_name()).is_dir():
        sys.exit(f"Antares binaries not found at {PROJECT_ROOT / get_antares_dir_name()}")
    if not (PROJECT_ROOT / get_antares_xpansion_dir_name()).is_dir():
        sys.exit(f"Antares Xpansion binaries not found at {PROJECT_ROOT / get_antares_xpansion_dir_name()}")

    pypsa_solver_name = args.pypsa_solver_name or ("xpress" if args.gems_solver_name == "xpress" else "cbc")

    if "xpress" in (args.gems_solver_name, pypsa_solver_name):
        if args.xpress_license_path is None:
            sys.exit(
                "Xpress solver requested (--gems-solver-name and/or --pypsa-solver-name) but no "
                "--xpress-license-path given -- without it, xpress silently runs under the "
                "Community edition and fails deep into the run instead of failing clearly now."
            )
        logger.info("Activating Xpress license: %s", args.xpress_license_path)
        activate_and_verify(args.xpress_license_path)

    study_dir = args.study_dir or (PROJECT_ROOT / "tmp" / f"benchmark_{args.input.stem}")

    row = run_benchmark(
        args.input,
        study_dir,
        gems_solver_name=args.gems_solver_name,
        pypsa_solver_name=pypsa_solver_name,
        results_csv=args.results_csv,
        keep_study=args.keep_study,
        skip_pypsa_solve=args.skip_pypsa_solve,
    )

    print()
    print("=" * 80)
    for key, value in row.items():
        print(f"{key}: {value}")
    print("=" * 80)


if __name__ == "__main__":
    main()
