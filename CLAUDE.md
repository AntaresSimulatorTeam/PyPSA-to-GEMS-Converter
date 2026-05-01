# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

For full project context, conventions, architecture details, and critical rules, see [AGENTS.md](AGENTS.md) — read it before making any changes.

## Quick Reference

```bash
# Install
pip install -r requirements-dev.txt

# Unit tests (no Antares needed)
pytest -n auto tests/unit_tests/ -v -s --log-cli-level=INFO

# Single test
pytest tests/unit_tests/tests_converter.py::test_converter_deterministic_study -v

# E2E tests (require Antares binary on PATH — version in dependencies.json)
pytest -n auto tests/e2e/end_2_end_tests.py -v -s --log-cli-level=INFO

# Lint & type-check
ruff check src tests && ruff format src tests && mypy src && mypy tests
```

## Key Reminders

- Antares binary version is tracked in `dependencies.json` (currently 9.3.7), CI downloads it automatically
- If you want to execute e2e tests locally you need to download proper Antares Simulator version mentioned inside `dependencies.json`
- Never pass an optimized network (`network.optimize()`) to `PyPSAStudyConverter` — HiGHS solver state cannot be deep-copied
- Output component IDs differ from PyPSA input IDs (type prefix added, spaces replaced with `_`)
- `resources/pypsa_models/pypsa_models.yml` parameter counts must exactly match `PyPSARegister` mappings — mismatch causes silent Antares modeler failure
- All studies use the multi-scenario code path internally, e.g. deterministic = single `"default"` scenario
- Git workflow: feature branches from `main`, PRs back to `main`
