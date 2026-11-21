# AGENTS.md

This file provides guidance to AI coding agents (LLMs, copilots, code assistants) when working with this repository. It is tool-agnostic and should be read by any AI agent before making changes.

---

## Project Overview

**PyPSA-to-GEMS Converter** converts [PyPSA](https://pypsa.org/) energy system networks into [GEMS](https://gems-energy.readthedocs.io/) study folders runnable by the Antares Simulator modeler. It handles conversion of linear optimal power flow, economic dispatch, and two-stage stochastic optimization studies, with documented [global constraints](COMPATIBILITY.md#globalconstraints) and [unsupported components](COMPATIBILITY.md#unsupported-components).

Repository: `AntaresSimulatorTeam/PyPSA-to-GEMS-Converter` — License: MPL 2.0

---

## Directory Layout

```
src/
  pypsa_converter.py          # PyPSAStudyConverter: top-level orchestrator
  pypsa_preprocessor.py       # PyPSAPreprocessor: 
- Validates converter limitations 
- Adds a fictitious null carrier 
- Rename busses 
- Preprocessed each **supported** component type  
 
  pypsa_register.py           # PyPSARegister: 

-  Registers each **supported** component type 
-  Registers global constraints 
  gems_model_builder.py       # GemsModelBuilder: 
-  Build GEMS components
-  Build port connections
-  Handles global constraints separately 
  time_granularity.py         # Maps PyPSA `snapshot_weightings` onto the GEMS time-granularity
                              # parameters (`hours_per_time_step`, `objective_weighting`, `standing_efficiency`):
                              # validates them and writes them onto the components that carry them
  optim_config.py             # build_optim_config(): optim-config.yml from the models used by the study
                              # (out-of-bounds `drop` for ramps / non-cyclic storage, Benders decomposition)
  gems_study_writer.py        # GemsStudyWriter: 
- Writes `system.yml`- GEMS components and port connections 
- Writes `parameters.yml` (`parameters.yml`) — see [modeler parameters](https://gems-energy.readthedocs.io/en/latest/3_User_Guide/3_GEMS_File_Structure/6_solver_optimization/).
- Copies `pypsa-models.yml` - copies pre-built PyPSA GEMS model library
- Writes `optim-config.yml` (built by `src/optim_config.py`) for investment studies and for studies with ramp limits or non-cyclic storage
- Writes shared data series once (time-varying snapshot weightings, last-time-step indicator)
- Preproceses and writes time series data into `data-series` directory 
  converter_limitations.py    # check_converter_limitations(): rejects PyPSA features the converter doesn't model;
                              # drop_inactive_components(): removes active=False components, as PyPSA does
  utils.py                    # Data conversion helpers (PyPSA pandas → Polars)
  dependencies.py             # Resolve Antares binary paths from dependencies.json
  cli.py                      # CLI entry point (`pypsa-to-gems = src.cli:main`): parses args, runs PyPSAStudyConverter
  models/
    modified_base_model.py    # ModifiedBaseModel: Pydantic base model applied across all internal models
    gems_system_yml_schema/   #  models for system.yml output
    pypsa_model_schema/       # Containers for PyPSA data
    modeler_parameter_yml_schema/  # AntaresModelerParameters for parameters.yml
resources/
  pypsa_models/
    pypsa_models.yml          # GEMS model library used by all converted studies
  test_files/                 # PyPSA .nc network files for E2E tests
tests/
  unit_tests/                 # Conversion logic tests (no Antares binary needed)
  e2e/                        # E2E tests: convert + run Antares + compare objectives
  e2e_2_stage_stochastic/     # E2E tests for stochastic (Benders) studies
  local_benchmark/            # Benchmark suite with Jupyter notebook for analysis
  utils.py                    # Shared test helpers
```

---

## Conversion Pipeline

Orchestrated by `PyPSAStudyConverter` (`src/pypsa_converter.py`):

```
PyPSA Network (deep-copied)
  → drop_inactive_components()   remove active=False components (after the "default" scenario is set)
  → PyPSAPreprocessor   validate limitations, rename components, add fictitious carrier, pin *_nom_set,
                        periodize costs, prepare multi-port / ramp / time-granularity parameters
  → PyPSARegister       route each component to a GEMS model variant, extract its static/dynamic data,
                        map to GEMS param names, build the global constraints
  → GemsModelBuilder    build GemsComponent + GemsPortConnection objects
  → GemsStudyWriter     write system.yml, parameters.yml, data-series/, optim-config.yml
```

**Model variants:** GEMS has no model inheritance, so a PyPSA component type whose options change the equations maps onto several close GEMS models, and `PyPSARegister` routes each component to one of them: `generator` / `generator_ramp`, `link` / `link_ramp` / `link_multiport` / `link_multiport_ramp` (Links with `bus2`..`bus4` and every `Process`), `storage_unit` / `storage_unit_non_cyclic`, `store` / `store_non_cyclic`. Component data is keyed `"<component type>:<gems model>"` (e.g. `"links:link_multiport"`). When you change a model, change its variants too.

**Boundary handling:** PyPSA does not wrap ramp limits or non-cyclic storage balances around the horizon. The `*_ramp` and `*_non_cyclic` models are listed in `optim-config.yml` with `out-of-bounds-processing: drop` (written by `src/optim_config.py`; the file format with only `models:` is read by GemsPy and antares-modeler 10.1/10.2), plus an explicit `[0]` initial constraint. The final storage level, needed by global constraints, comes from the shared `is_last_time_step` series (antares-modeler rejects a parameter as explicit time index).

**Shared data series:** parameters equal for every component (a time-varying `snapshot_weightings` column, the last-time-step indicator) are written once (`GemsStudyWriter.write_shared_series()`) and referenced by every component through `PyPSAComponentData.shared_params`, instead of one file per component.

**Global constraints:** one GEMS component per PyPSA GlobalConstraint (whatever the number of scenarios, with a scenario-dependent `quota`), connected to the ports of the components PyPSA sums: `global_constraint_co2_*` (`primary_energy`), `global_constraint_quantity_*` (`operational_limit`, PyPSA-Eur `co2_atmosphere`), `global_constraint_capacity_*` (transmission expansion limits, `tech_capacity_expansion_limit`, bus `nom_min_`/`nom_max_<carrier>`). A constraint with no contributor is skipped, as PyPSA does; an unknown type is ignored with a warning, as PyPSA ignores it.

### Key design decisions

**Scenario handling:** All studies — including deterministic — go through `pypsa_network.set_scenarios()`. A deterministic network gets a single `"default"` scenario injected by `determine_pypsa_study_type()` in `src/utils.py`. The converter counts scenario weightings: >1 triggers `optim-config.yml` generation for Benders decomposition.

**Component renaming:** `PyPSAPreprocessor._rename_pypsa_component()` adds a component-type prefix and replaces spaces with underscores (e.g., PyPSA `"gen 1"` → GEMS `"generator_gen_1"`). Bus names are similarly space-normalized. **GEMS output IDs are never identical to PyPSA input IDs.**

**Time granularity:** the converter supports any time granularity, read **only** from
`network.snapshot_weightings` — never inferred from the spacing of the snapshot index (PyPSA itself
treats a weighting of 1 as one hour whatever the index says). Each GEMS model reads the column PyPSA
reads for the same component (`src/time_granularity.py`): `hours_per_time_step` is the `generators`
column for generator models (`e_sum_*`, primary energy, operational limit) and the `stores` column for
storage models (balances); `objective_weighting` is the `objective` column. The columns may differ and
may vary over snapshots (temporal clustering): a constant column is a scalar in `system.yml`, a varying
one a shared data series. `resolve_time_weights()` validates **only the columns this network actually
reads** (finite, positive durations, non-negative objective weights).

The compounded standing loss `(1 - standing_loss)^hours` is computed by the converter
(`standing_efficiency`): antares-modeler only accepts constant exponents in `^`.

**CO₂ emission factors:** For emission-bearing component types (`_EMISSION_FACTOR_COMPONENTS`), the preprocessor resolves each component's `emission_factor` from `network.carriers` via `_carrier_attribute_by_scenario()` (`src/pypsa_preprocessor.py`), using the carrier attribute of the `primary_energy` global constraints (`co2_emissions` by default; a single attribute per network), and preserving per-scenario values when `carriers` is scenario-indexed (MultiIndex). Components with no carrier get a fictitious `null` carrier with `co2_emissions=0`.

**Data format:** Static data is converted from pandas to Polars via `static_pypsa_to_polars()`; time-series data via `dynamic_dict_pypsa_to_polars()`. PyPSA objects remain as pandas internally; only the Polars copies are used downstream.

**Float clamping:** `any_to_float()` (in `src/utils.py`) clamps all floats to `±PYPSA_CONVERTER_MAX_FLOAT` (100 billion, i.e. `1e11`). Separately, `GemsStudyWriter` writes unbounded `inf`/`-inf` bounds as `±1e20` in output files (`src/gems_study_writer.py`) — a distinct mechanism from `any_to_float()`.

**Time series naming:** Files written to `systems/input/data-series/` follow the pattern `{system_name}_{component}_{param}.csv`, made of `[A-Za-z0-9_-]` only by `series_name()` (PyPSA-Eur network names hold URLs, and GemsPy cuts a series name at its last dot). Shared series are `{system_name}_{key}` (e.g. `_snapshot_weightings_objective`, `_last_time_step_indicator`). Multi-scenario dynamic columns use `__` as separator: `scenario__component`.

### Output directory layout

```
<study_dir>/
  systems/
    input/
      system.yml
      model-libraries/
        pypsa_models.yml
      optim-config.yml          # investment studies (Benders), or ramp limits / non-cyclic storage
      data-series/
        {system}_{component}_{param}.csv
    parameters.yml
```

---

## GEMS Model Library (`resources/pypsa_models/pypsa_models.yml`)

This file defines all component models and their variants (bus, load, generator, generator_ramp, line, transformer, link, link_ramp, link_multiport, link_multiport_ramp, storage_unit, storage_unit_non_cyclic, store, store_non_cyclic, global_constraint_{co2,quantity,capacity}_{max,eq,min}). It is copied into every converted study's `model-libraries/` directory. `tests/unit_tests/test_sector_coupling_features.py` checks that every converted component carries exactly its model's parameters.

**Critical:** the parameter list in this YAML must exactly match what `PyPSARegister.register()` maps for each component type. A mismatch causes the Antares modeler to fail silently.

---

## Supported / Unsupported Components

| Supported | Not Supported |
|-----------|---------------|
| buses, loads (incl. negative), generators, links (up to bus4), processes, storage_units, stores, lines, transformers | quadratic costs (non-linear), `committable=True` (PR #117), maintenance, modular expansion of generators/links/storage, fixed dispatch (`p_set`, `e_set`, …) |
| ramp limits, non-cyclic storage, time-varying snapshot weightings, `*_nom_set`, overnight/fom costs, inactive components (dropped) | multi-investment periods, piecewise curves, CVaR (GEMS roadmap) |
| global constraints `primary_energy`, `operational_limit`, `co2_atmosphere` (PyPSA-Eur), `transmission_*_limit`, `tech_capacity_expansion_limit`, bus `nom_*_<carrier>` | link delays, transformer phase shifts, `v_ang_max`, `transmission_losses` and `extra_functionality` (not in the network) |

Inactive components (`active=False`) are dropped, as PyPSA does. Lines and Transformers are converted **natively**: their `type` (LineTypes/TransformerTypes) is resolved into reactance by PyPSA's `calculate_dependent_values()` during preprocessing — see [pypsa_preprocessor.py](src/pypsa_preprocessor.py) and the authoritative limitations list in [COMPATIBILITY.md](COMPATIBILITY.md#converter-limitations). The `replace_lines_by_links()` helper in `tests/utils.py` (bidirectional link pairs with `p_min_pu=-1`) is still available for tests that prefer to model lines as links, but is no longer required.

---

## Running Tests

### Prerequisites

- **Antares Simulator binary** (the `antares-modeler`, version in `dependencies.json`). CI downloads it automatically. For local runs, extract the archive at the repo root — the path is resolved by `src/dependencies.py`. Both the standard and stochastic E2E tests currently invoke `antares-modeler` and skip if it is not installed.
- **Antares Xpansion (benders):** helpers exist in `src/dependencies.py`, but the Xpansion/benders solve in the stochastic E2E test is currently commented out, so Xpansion is **not** exercised at present.

### Commands

```bash
uv sync --group dev          # installs/updates .venv dev dependencies

# Unit tests (no Antares needed)
uv run pytest -n auto tests/unit_tests/ -v -s --log-cli-level=INFO

# Single test
uv run pytest tests/unit_tests/tests_converter.py::test_converter_deterministic_study -v

# E2E tests
uv run pytest -n auto tests/e2e/end_2_end_tests.py -v -s --log-cli-level=INFO

# E2E tests of the sector-coupling features (PyPSA-Eur, issue #126)
uv run pytest -n auto tests/e2e/test_sector_coupling_e2e.py -v -s --log-cli-level=INFO

# Stochastic E2E tests
uv run pytest -n auto tests/e2e_2_stage_stochastic/ -v -s --log-cli-level=INFO

# Lint + type check
uv run ruff check src tests && uv run ruff format src tests && uv run mypy src && uv run mypy tests

# Pre-commit
uv run pre-commit run --all-files
```

### Test architecture

| Directory | What it tests | Antares needed? |
|-----------|---------------|-----------------|
| `tests/unit_tests/` | Conversion logic, data mapping, YAML output | No |
| `tests/e2e/` | Convert → run modeler → compare objective values | Yes |
| `tests/e2e_2_stage_stochastic/` | Stochastic conversion; runs `antares-modeler` (Benders/Xpansion solve is currently commented out) | Yes (modeler) |
| `tests/local_benchmark/` | Performance profiling; Jupyter notebook for analysis | Yes |

E2E tests load `.nc` files from `resources/test_files/`, call `PyPSAStudyConverter`, run `antares-modeler` via `subprocess`, and parse the objective value from the output CSV/TSV using `get_objective_value()` in `tests/utils.py`.

---

## CI Workflows

| Workflow | Trigger | What it does |
|----------|---------|--------------|
| `tests.yml` | Push, PR | Downloads Antares binary, runs unit + E2E tests |
| `code-quality.yml` | Push, PR | Runs ruff + mypy |

---

## Git & Branching Model

- **`main`** — primary branch; PRs target `main`
- Feature branches from `main`, no direct pushes

> **Full ecosystem developer guide:** the authoritative branching, versioning, CI/CD, and release process for all repositories (including this converter) lives in the GEMS Developer Guidelines, published at <https://gems-energy.readthedocs.io/en/latest/support/dev-guidelines/>. Fetch this page (e.g. via WebFetch) before any branching, versioning, or release work.

---

## Coding Conventions

- Type hints everywhere; `pathlib.Path` for file paths
- Pydantic models inherit from `ModifiedBaseModel` which applies an alias generator transforming `_` → `-` for all YAML keys
- `GemsSystem` and `AntaresModelerParameters` use `PrivateAttr` for their core data fields — an unusual Pydantic pattern; serialization is done via manual `to_dict()` / `to_yml()` methods
- Logging via `logging.getLogger(__name__)` — no bare `print()`
- **Ruff**: line-length 120, Python 3.11, rules E4/E7/E9/F/I (config in `pyproject.toml`)
- **mypy**: strict mode with Pydantic plugin; `src/models/` has `ignore_errors = true`

---

## Critical Rules for AI Agents

1. **Never pass an optimized network to `PyPSAStudyConverter`.** Calling `network.optimize()` attaches HiGHS solver state that cannot be `deepcopy()`'d. This causes a runtime error in the constructor. Always create a fresh network or reload it from file before converting.

2. **GEMS output component IDs differ from PyPSA input IDs.** The preprocessor adds a type prefix and replaces spaces (e.g., `"gen 1"` → `"generator_gen_1"`). If you are writing tests or debugging output files, do not assume PyPSA names appear verbatim in `system.yml`.

3. **`resources/pypsa_models/pypsa_models.yml` parameter ids must exactly match `PyPSARegister`.** If you add a parameter to the register's mapping dict for a component type, you must add the corresponding parameter definition to the YAML model. The Antares modeler fails silently (no exception, no output) on a mismatch.

4. **`check_converter_limitations()` raises before any output is written.** It runs in the `PyPSAStudyConverter` constructor; output is only written later by `to_gems_study()`. If a network violates a constraint (e.g., uses quadratic costs, `committable=True`, fixed dispatch, or non-empty `investment_periods`), the converter raises `ValueError` at construction time. No partial output is produced. All checks live in `src/converter_limitations.py`, called by `PyPSAPreprocessor`: each one compares against PyPSA's default value, all findings are reported in one `ValueError`, and features that don't change the optimum (`v_ang_min`, unknown global constraint types) only log a warning. Inactive components are dropped before the checks (`drop_inactive_components()`), so their attributes are never checked. See [COMPATIBILITY.md](COMPATIBILITY.md#converter-limitations).

5. **The Antares modeler fails silently.** `subprocess.run` in E2E tests uses `check=False` with `capture_output=True`. If the modeler exits non-zero, no output directory is created and the test fails with `FileNotFoundError` on the objective value file. To debug, run the modeler binary directly and inspect stderr.

6. **CO₂ emission factors come from `network.carriers`.** For emission-bearing components, the preprocessor resolves each component's `co2_emissions` from the carrier definitions via `_carrier_co2_by_scenario()`, preserving per-scenario values when `carriers` is scenario-indexed. So define carriers with the correct `co2_emissions` (or the carrier attribute of the `primary_energy` global constraints) if you need emission factors in the output; components with no carrier get a fictitious `null` carrier (`co2_emissions=0`).

7. **All studies use the multi-scenario code path internally.** Even deterministic studies are converted with a single `"default"` scenario. There is no "deterministic vs stochastic" branch in the data-handling code — only the final `optim-config.yml` generation differs.

8. **Global constraints follow PyPSA's selection rules.** Each supported type connects the ports of exactly the components PyPSA sums (see `PyPSARegister._register_pypsa_globalconstraints()`); `co2_atmosphere` is PyPSA-Eur's convention, which plain `n.optimize()` ignores — E2E tests compare it with PyPSA run with the equivalent `extra_functionality`. Unknown types are ignored with a warning, as PyPSA does.

9. **Float clamping is intentional.** `any_to_float()` clamps values to `±100_000_000_000`. Do not remove or weaken this: the Antares modeler requires bounded values. `inf`/`-inf` must not appear in output files.

10. **Do not commit** `tmp/` output files, or extracted Antares binary directories.

11. **Version tracking.** The Antares Simulator version is in `dependencies.json`. When updating, also check `src/dependencies.py` defaults and CI workflow download steps.

12. **Floating-point comparisons in tests.** Always use `pytest.approx()`. E2E tests compare PyPSA vs GEMS objective values — small LP/solver differences are expected; use relative tolerance.

---

## Key Dependencies

| Package | Purpose |
|---------|---------|
| `pypsa` | Source network format |
| `polars` | Internal DataFrame representation for static + dynamic data |
| `pandas` | Used by PyPSA; converted to Polars before processing |
| `pydantic` | Schema validation for all output model classes |
| `PyYAML` | YAML serialization for system.yml and parameters.yml |
| `highspy` | Default LP solver used by PyPSA (also default in `PyPSAStudyConverter`) |

---

## Related Projects

| Project | Relationship |
|---------|-------------|
| [GEMS](https://github.com/AntaresSimulatorTeam/GEMS) | Defines the target YAML modelling language and model library structure |
| [Antares Simulator](https://github.com/AntaresSimulatorTeam/Antares_Simulator) | Provides `antares-modeler` binary used in E2E validation |
| [AntaresLegacyModels-to-GEMS-Converter](https://github.com/AntaresSimulatorTeam/AntaresLegacyModels-to-GEMS-Converter) | Sibling converter for Antares legacy studies |
