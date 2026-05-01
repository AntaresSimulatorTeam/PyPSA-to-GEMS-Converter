# AGENTS.md

This file provides guidance to AI coding agents (LLMs, copilots, code assistants) when working with this repository. It is tool-agnostic and should be read by any AI agent before making changes.

---

## Project Overview

**PyPSA-to-GEMS Converter** converts [PyPSA](https://pypsa.org/) energy system networks into [GEMS](https://gems-energy.readthedocs.io/) study folders runnable by the Antares Simulator modeler. It handles conversion of linear optimal power flow, economic dispatch, and two-stage stochastic optimization studies, with documented [constraints](https://github.com/AntaresSimulatorTeam/PyPSA-to-GEMS-Converter/blob/main/README.md#global-constraints) and [currently unsupported components](https://github.com/AntaresSimulatorTeam/PyPSA-to-GEMS-Converter/blob/main/README.md#unsupported-pypsa-components).

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
  gems_study_writer.py        # GemsStudyWriter: 
- Writes `system.yml`- GEMS components and port connections 
- Writes `parameters.yml` (`parameters.yml`) — see [modeler parameters](https://gems-energy.readthedocs.io/en/latest/3_User_Guide/3_GEMS_File_Structure/6_solver_optimization/).
- Copies `pypsa-models.yml` - copies pre-built PyPSA GEMS model library
- Copies `optim-config.yml` -  Benders decomposition used by `Antares Modeler`
- Preproceses and writes time series data into `data-series` directory 
  utils.py                    # Data conversion helpers (PyPSA pandas → Polars)
  dependencies.py             # Resolve Antares binary paths from dependencies.json
  parsing.py                  # Legacy CLI argument parser (not used by converter)
  models/
    modified_base_model.py    # ModifiedBaseModel: Pydantic base with _ → - alias generator
    gems_system_yml_schema/   # Pydantic models for system.yml output
    pypsa_model_schema/       # PyPSAComponentData, PyPSAGlobalConstraintData dataclasses
    modeler_parameter_yml_schema/  # ModelerParameters for parameters.yml
resources/
  pypsa_models/
    pypsa_models.yml          # GEMS model library used by all converted studies
  test_files/                 # PyPSA .nc network files for E2E tests
  optim-config.yml            # Benders decomposition config (stochastic studies)
tests/
  unit_tests/                 # Conversion logic tests (no Antares binary needed)
  e2e/                        # E2E tests: convert + run Antares + compare objectives
  e2e_2_stage_stochastic/     # E2E tests for stochastic (Benders) studies
  local_benchmark/            # Benchmark suite with Jupyter notebook for analysis
  utils.py                    # Shared test helpers (load .nc, replace_lines_by_links, etc.)
```

---

## Conversion Pipeline

Orchestrated by `PyPSAStudyConverter` (`src/pypsa_converter.py`):

```
PyPSA Network (deep-copied)
  → PyPSAPreprocessor   validate limitations, rename components, add fictitious carrier
  → PyPSARegister       extract static/dynamic data per component type, map to GEMS param names
  → GemsModelBuilder    build GemsComponent + GemsPortConnection objects
  → GemsStudyWriter     write system.yml, parameters.yml, data-series/, optim-config.yml
```

### Key design decisions

**Scenario handling:** All studies — including deterministic — go through `pypsa_network.set_scenarios()`. A deterministic network gets a single `"default"` scenario injected by `determine_pypsa_study_type()` in `src/utils.py`. The converter counts scenario weightings: >1 triggers `optim-config.yml` generation for Benders decomposition.

**Component renaming:** `PyPSAPreprocessor._rename_pypsa_component()` adds a component-type prefix and replaces spaces with underscores (e.g., PyPSA `"gen 1"` → GEMS `"generator_gen_1"`). Bus names are similarly space-normalized. **GEMS output IDs are never identical to PyPSA input IDs.**

**CO₂ snapshotting:** Carrier CO₂ emission values are captured into `network._carrier_co2_snapshot` before `set_scenarios()` is called, because PyPSA overwrites them during scenario expansion.

**Data format:** Static data is converted from pandas to Polars via `static_pypsa_to_polars()`; time-series data via `dynamic_dict_pypsa_to_polars()`. PyPSA objects remain as pandas internally; only the Polars copies are used downstream.

**Float clamping:** `any_to_float()` (in `src/utils.py`) clamps all floats to `±PYPSA_CONVERTER_MAX_FLOAT` (100 billion). `inf`/`-inf` become `1e20`/`-1e20` in output files.

**Time series naming:** Files written to `systems/input/data-series/` follow the pattern `{system_name}_{component}_{param}.csv`. Multi-scenario dynamic columns use `__` as separator: `scenario__component`.

### Output directory layout

```
<study_dir>/
  systems/
    input/
      system.yml
      model-libraries/
        pypsa_models.yml
      optim-config.yml          # only for stochastic studies (>1 scenario)
      data-series/
        {system}_{component}_{param}.csv
    parameters.yml
```

---

## GEMS Model Library (`resources/pypsa_models/pypsa_models.yml`)

This file defines all component models (generator, load, bus, link, storage_unit, store, global_constraint_co2_max, global_constraint_co2_eq). It is copied into every converted study's `model-libraries/` directory.

**Critical:** the parameter list in this YAML must exactly match what `PyPSARegister.register()` maps for each component type. A mismatch causes the Antares modeler to fail silently.

---

## Supported / Unsupported Components

| Supported | Not Supported |
|-----------|---------------|
| generators, loads, buses, links, storage_units, stores | Lines, Transformers |
| global_constraints (`co2_emissions`, `primary_energy` type, `<=` or `==` sense) | quadratic costs, `committable=True`, non-cyclic state of charge |

All components must have `active=1`. Lines can be approximated as links using `replace_lines_by_links()` from `tests/utils.py` (bidirectional link pairs with `p_min_pu=-1`).

---

## Running Tests

### Prerequisites

- **Antares Simulator binary** (version in `dependencies.json`, currently 9.3.7). CI downloads it automatically. For local runs, extract the archive at the repo root — the path is resolved by `src/dependencies.py`.
- **Antares Xpansion** (v1.8.0) for stochastic E2E tests.

### Commands

```bash
pip install -r requirements-dev.txt

# Unit tests (no Antares needed)
pytest -n auto tests/unit_tests/ -v -s --log-cli-level=INFO

# Single test
pytest tests/unit_tests/tests_converter.py::test_converter_deterministic_study -v

# E2E tests
pytest -n auto tests/e2e/end_2_end_tests.py -v -s --log-cli-level=INFO

# Stochastic E2E tests
pytest -n auto tests/e2e_2_stage_stochastic/ -v -s --log-cli-level=INFO

# Lint + type check
ruff check src tests && ruff format src tests && mypy src && mypy tests

# Pre-commit
pre-commit run --all-files
```

### Test architecture

| Directory | What it tests | Antares needed? |
|-----------|---------------|-----------------|
| `tests/unit_tests/` | Conversion logic, data mapping, YAML output | No |
| `tests/e2e/` | Convert → run modeler → compare objective values | Yes |
| `tests/e2e_2_stage_stochastic/` | Stochastic conversion + Benders solve | Yes + Xpansion |
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

---

## Coding Conventions

- Type hints everywhere; `pathlib.Path` for file paths
- Pydantic models inherit from `ModifiedBaseModel` which applies an alias generator transforming `_` → `-` for all YAML keys
- `GemsSystem` and `ModelerParameters` use `PrivateAttr` for their core data fields — an unusual Pydantic pattern; serialization is done via manual `to_dict()` / `to_yml()` methods
- Logging via `logging.getLogger(__name__)` — no bare `print()`
- **Ruff**: line-length 120, Python 3.11, rules E4/E7/E9/F/I (config in `pyproject.toml`)
- **mypy**: strict mode with Pydantic plugin; `src/models/` has `ignore_errors = true`

---

## Critical Rules for AI Agents

1. **Never pass an optimized network to `PyPSAStudyConverter`.** Calling `network.optimize()` attaches HiGHS solver state that cannot be `deepcopy()`'d. This causes a runtime error in the constructor. Always create a fresh network or reload it from file before converting.

2. **GEMS output component IDs differ from PyPSA input IDs.** The preprocessor adds a type prefix and replaces spaces (e.g., `"gen 1"` → `"generator_gen_1"`). If you are writing tests or debugging output files, do not assume PyPSA names appear verbatim in `system.yml`.

3. **`resources/pypsa_models/pypsa_models.yml` parameter counts must exactly match `PyPSARegister`.** If you add a parameter to the register's mapping dict for a component type, you must add the corresponding parameter definition to the YAML model. The Antares modeler fails silently (no exception, no output) on a mismatch.

4. **`check_converter_limitations()` raises before any output is written.** If a network violates a constraint (e.g., contains Lines, uses quadratic costs, has `active=0` components), the converter raises `ValueError` or `AssertionError` at startup. No partial output is produced.

5. **The Antares modeler fails silently.** `subprocess.run` in E2E tests uses `check=False` with `capture_output=True`. If the modeler exits non-zero, no output directory is created and the test fails with `FileNotFoundError` on the objective value file. To debug, run the modeler binary directly and inspect stderr.

6. **CO₂ carrier values must be set before `set_scenarios()`.** If you build a PyPSA network and need emission factors to appear in the converted study, add carriers with `co2_emissions` before calling `set_scenarios()`. The preprocessor snapshots carrier values prior to scenario expansion because PyPSA resets them afterward.

7. **All studies use the multi-scenario code path internally.** Even deterministic studies are converted with a single `"default"` scenario. There is no "deterministic vs stochastic" branch in the data-handling code — only the final `optim-config.yml` generation differs.

8. **Global constraints are strictly limited.** Only `carrier_attribute == "co2_emissions"` with `type == "primary_energy"` and sense `<=` or `==` is supported. Any other constraint type raises `ValueError` in `_register_pypsa_globalconstraints()`.

9. **Float clamping is intentional.** `any_to_float()` clamps values to `±100_000_000_000`. Do not remove or weaken this: the Antares modeler requires bounded values. `inf`/`-inf` must not appear in output files.

10. **Do not commit** `venv/`, `tmp/`, `.nc` output files, or extracted Antares binary directories.

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
