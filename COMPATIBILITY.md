# PyPSA-to-GEMS Converter — Compatibility Matrix

This table maps converter versions to the PyPSA and Antares-Simulator versions they are compatible with.

| Converter | PyPSA | Antares-Simulator | Notes |
|-----------|-------|-------------------|-------|
| 0.0.1     | 1.0.0 | 9.3.7             | Initial release |

## Versioning Policy

- **Converter** — version in `pyproject.toml` (`[project] version`). Follows semantic versioning:
  - **Major** — Antares-Simulator major version bump
  - **Minor** — Bug fix, new PyPSA feature supported, or PyPSA version update
  - **Patch** — Dependency updates, code optimisation, or PyPSA models library-only change

- **PyPSA Models Library** — version tracked in `resources/pypsa_models/CHANGELOG-pypsa_models_library.md` (latest entry header). Independent versioning:
  - **Major** — New PyPSA model added to `pypsa_models.yml`
  - **Minor** — Bug fix or improvement to an existing model
  - **Patch** — Non-functional change (rename variable/parameter, internal refactor)

- **PyPSA** — tracked version in `requirements.txt` (pinned via `pypsa==x.y.z`). The version against which E2E tests are run.

- **Antares-Simulator** — tracked version in `dependencies.json` (`antares_version`). The version downloaded by CI and used for E2E tests.

## Compatibility Rules

- Patch versions are always backward-compatible within the same Major.Minor.
- Upgrading PyPSA or Antares may require a converter Minor or Major bump — see `CHANGELOG-pypsa_models_library.md` and git history for details.

## Version Files

| Component | Current Version | Version File |
|-----------|----------------|--------------|
| Converter | 0.0.1 | `pyproject.toml` |
| PyPSA Models Library | 1.0.0 | `resources/pypsa_models/CHANGELOG-pypsa_models_library.md` |
| PyPSA | 1.0.0 | `requirements.txt` |
| Antares-Simulator | 9.3.7 | `dependencies.json` |
