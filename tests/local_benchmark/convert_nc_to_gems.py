"""Convert any PyPSA .nc network file into a GEMS study, ready for Antares-Xpansion
(Benders, coin/CBC or xpress solver), applying every fix established while getting a
real stressful-weather 2-scenario network to convert cleanly:

1. Strip solver-output data (dispatch, duals, p_nom_opt, objective, and any attribute
   absent from PyPSA's own schema) -- so a previously-solved .nc converts as a fresh,
   unsolved study regardless of what state the input file was saved in.
2. Drop any global constraint this converter doesn't support (only
   type=="primary_energy" and carrier_attribute=="co2_emissions" are supported --
   see AGENTS.md Critical Rule #8 / src/pypsa_preprocessor.py's assertion).
3. Homogenize static (non-time-varying) parameters across scenarios, using the first
   scenario as canonical -- the GEMS model library (resources/pypsa_models/pypsa_models.yml)
   declares almost all static parameters "scenario-dependent: false"; even tiny
   floating-point noise between scenarios (e.g. offshore wind capital_cost recomputed
   per weather-year source build) makes the Antares Modeler reject the study with
   "Scenario dependance mismatch between model and system". Only the handful of
   parameters the model explicitly allows to vary by scenario (parsed dynamically from
   the model library, e.g. generator e_sum_min/e_sum_max) are left untouched.
4. Validate the horizon is a whole number of Antares weeks (n_timesteps % 168 == 0) --
   otherwise PyPSAStudyConverter silently SKIPS writing the Xpansion-runnable hybrid
   study (just logs a warning), which would only be noticed later when trying to run
   antares-xpansion-launcher on the converted study and finding no input for it.

This only performs the CONVERSION step (PyPSA -> GEMS study on disk), not solving --
solving (antares-problem-generator + benders) is left to the caller (e.g. cloud
infrastructure, or benchmark_pypsa_vs_xpansion.py in this directory). See --solve for
an optional local dry-run of that using the same HiGHS MPS re-emit fix validated in
tests/e2e/test_hybrid_study_comparison.py, needed because real PyPSA-Eur networks have
buses with no Line/Transformer connection (mostly battery/H2 storage bookkeeping buses)
whose theta variable Antares' own MPS writer declares only in BOUNDS, never in COLUMNS
-- which Coin/Clp rejects outright.

Usage:
  uv run python tests/local_benchmark/convert_nc_to_gems.py --input NETWORK.nc --output STUDY_DIR
  uv run python tests/local_benchmark/convert_nc_to_gems.py --input NETWORK.nc --output STUDY_DIR --solve
"""

import argparse
import logging
import shutil
import subprocess
import sys
from pathlib import Path

import highspy
import pandas as pd
import pypsa
import yaml

PROJECT_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(PROJECT_ROOT))

from src.dependencies import get_antares_xpansion_launcher_bin  # noqa: E402
from src.pypsa_converter import PyPSAStudyConverter  # noqa: E402
from src.utils import (  # noqa: E402
    _latest_simulation_dir,
    _parse_xpansion_settings_ini,
    _read_yearly_weights,
    _write_benders_options,
    _XPANSION_SOLVER_TO_BENDERS,
    read_xpansion_out_json,
    write_benders_lp_weights,
)

MODEL_LIBRARY_PATH = PROJECT_ROOT / "resources" / "pypsa_models" / "pypsa_models.yml"
HOURS_PER_ANTARES_WEEK = 168

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger("convert_nc_to_gems")


def strip_outputs(n: pypsa.Network) -> None:
    """Reset every solver-output attribute back to an unsolved, pre-optimization state."""
    for c in n.components.values():
        if c.static.empty:
            continue
        defaults = c.defaults
        for col in list(c.static.columns):
            is_output = col not in defaults.index or defaults.loc[col, "status"] == "Output"
            if is_output:
                default_val = defaults.loc[col, "default"] if col in defaults.index else 0.0
                c.static[col] = default_val
        for key in list(c.dynamic.keys()):
            is_output = key not in defaults.index or defaults.loc[key, "status"] == "Output"
            if is_output and c.dynamic[key].shape[1] > 0:
                c.dynamic[key] = pd.DataFrame(index=c.dynamic[key].index)
    n._objective = None
    n._objective_constant = None


def drop_unsupported_global_constraints(n: pypsa.Network) -> None:
    """Keep only constraints this converter supports; drop (and log) everything else."""
    if n.global_constraints.empty:
        return
    supported_mask = (n.global_constraints["type"] == "primary_energy") & (
        n.global_constraints["carrier_attribute"] == "co2_emissions"
    )
    unsupported_names = n.global_constraints.index[~supported_mask]
    seen: set[str] = set()
    for name in unsupported_names:
        gc_name = name[-1] if isinstance(name, tuple) else name
        if gc_name in seen:
            continue
        seen.add(gc_name)
        logger.warning(
            "Dropping unsupported global constraint %r (type=%s) -- this converter only "
            "supports primary_energy/co2_emissions constraints",
            gc_name,
            n.global_constraints.loc[name, "type"],
        )
        n.remove("GlobalConstraint", gc_name)


def get_scenario_varying_static_params() -> set[str]:
    """Parameter ids the GEMS model library allows to vary by scenario while static
    (time-dependent: false, scenario-dependent: true) -- everything else must be
    scenario-invariant or the Antares Modeler rejects the study. Parsed from the model
    library itself so this stays correct if the library changes."""
    data = yaml.safe_load(MODEL_LIBRARY_PATH.read_text())
    exempt: set[str] = set()
    for model in data["library"].get("models", []):
        for param in model.get("parameters", []):
            if param.get("scenario-dependent") is True and param.get("time-dependent") is not True:
                exempt.add(param["id"])
    return exempt


def homogenize_static_across_scenarios(n: pypsa.Network, exempt_cols: set[str]) -> None:
    """Force every non-exempt static column to the first scenario's value everywhere.

    No-op (per column, per component) wherever values already agree across scenarios --
    safe to run on an already-clean file. canonical_label is n.scenarios' first entry,
    i.e. whatever scenario ordering the file itself carries.
    """
    if not n.has_scenarios or len(n.scenarios) <= 1:
        return
    canonical_label = n.scenarios[0]
    other_labels = list(n.scenarios[1:])

    for cname, c in n.components.items():
        if c.static.empty:
            continue
        canonical = c.static.loc[canonical_label]
        changed_cols: set[str] = set()
        for label in other_labels:
            target_index = c.static.loc[label].index
            aligned_canonical = canonical.reindex(target_index)
            for col in c.static.columns:
                if col in exempt_cols:
                    continue
                current = c.static.loc[(label, slice(None)), col]
                new_values = aligned_canonical[col].to_numpy()
                if not (pd.Series(current.to_numpy()).equals(pd.Series(new_values)) or current.empty):
                    changed_cols.add(col)
                c.static.loc[(label, slice(None)), col] = new_values
        if changed_cols:
            logger.info("%s: homogenized columns to %s's values: %s", cname, canonical_label, sorted(changed_cols))


def validate_hybrid_study_horizon(n: pypsa.Network) -> None:
    n_timesteps = len(n.snapshots)
    if n_timesteps % HOURS_PER_ANTARES_WEEK != 0:
        raise ValueError(
            f"Network has {n_timesteps} snapshots, not a multiple of {HOURS_PER_ANTARES_WEEK} "
            "(whole Antares weeks). PyPSAStudyConverter would silently SKIP writing the "
            "Xpansion-runnable hybrid study (only a warning, no error) -- fix the snapshot "
            "count before converting, or antares-xpansion-launcher will have nothing to run "
            "on the cloud side."
        )


def load_and_prepare(input_path: Path) -> pypsa.Network:
    logger.info("Loading %s", input_path)
    n = pypsa.Network(str(input_path))
    logger.info(
        "Loaded: %d buses, %d generators, %d lines, %d links, %d storage_units, %d stores, "
        "%d loads, %d snapshots, has_scenarios=%s%s",
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
    logger.info("Stripped solver-output data")

    drop_unsupported_global_constraints(n)

    exempt_cols = get_scenario_varying_static_params()
    homogenize_static_across_scenarios(n, exempt_cols)

    validate_hybrid_study_horizon(n)

    n.consistency_check()
    logger.info("consistency_check passed")
    return n


def convert(input_path: Path, output_dir: Path, *, solver_name: str, series_file_format: str) -> Path:
    n = load_and_prepare(input_path)

    logger.info("Converting to GEMS study at %s", output_dir)
    PyPSAStudyConverter(
        pypsa_network=n, study_dir=output_dir, series_file_format=series_file_format, solver_name=solver_name
    ).to_gems_study()
    logger.info("Conversion complete")

    study_name = n.name if n.name not in {"", None} else "pypsa_to_gems_converter"
    study_root = output_dir / study_name
    if study_root.is_dir():
        logger.info("Xpansion-runnable hybrid study: %s", study_root)
    else:
        logger.warning(
            "No hybrid study directory found at %s -- if this network has extendable "
            "capacity, check the logs above for why _write_antares_hybrid_study skipped it",
            study_root,
        )
    return study_root


# --- Optional local solve (same HiGHS MPS re-emit fix as tests/e2e/test_hybrid_study_comparison.py) ---


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

    logger.info("Running benders in %s", lp_dir)
    solved = subprocess.run(
        [str(benders), "options.json"], capture_output=True, text=True, check=False, cwd=str(lp_dir)
    )
    solved.stdout = (generated.stdout or "") + (solved.stdout or "")
    solved.stderr = (generated.stderr or "") + (solved.stderr or "")
    return solved


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--input", type=Path, required=True, help="Path to the source PyPSA .nc file")
    parser.add_argument("--output", type=Path, required=True, help="Study directory to write the GEMS study into")
    parser.add_argument("--solver-name", default="coin", choices=["coin", "xpress"])
    parser.add_argument("--series-file-format", default=".tsv")
    parser.add_argument(
        "--solve", action="store_true", help="Also run antares-problem-generator + benders locally (HiGHS-patched)"
    )
    args = parser.parse_args()

    if args.output.exists():
        logger.info("Removing existing output directory %s", args.output)
        shutil.rmtree(args.output)

    study_root = convert(
        args.input, args.output, solver_name=args.solver_name, series_file_format=args.series_file_format
    )

    if args.solve:
        launcher_bin = get_antares_xpansion_launcher_bin(PROJECT_ROOT)
        result = run_xpansion_launcher_with_highs_fix(study_root, launcher_bin)
        if result.returncode != 0:
            logger.error(
                "Solve FAILED (returncode=%s)\n--- stdout (tail) ---\n%s\n--- stderr (tail) ---\n%s",
                result.returncode,
                result.stdout[-4000:],
                result.stderr[-2000:],
            )
            sys.exit(1)
        solution = read_xpansion_out_json(study_root)["solution"]
        logger.info("Xpansion objective: %s (status=%s)", solution["overall_cost"], solution["problem_status"])


if __name__ == "__main__":
    main()
