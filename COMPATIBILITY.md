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

- **PyPSA Models Library** — version tracked in `resources/pypsa_models/pypsa_models.yml` (`library.version`). Independent versioning:
  - **Major** — New PyPSA model added to `pypsa_models.yml`
  - **Minor** — Bug fix or improvement to an existing model
  - **Patch** — Non-functional change (rename variable/parameter, internal refactor)

- **PyPSA** — tracked version in `requirements.txt` (pinned via `pypsa==x.y.z`). The version against which E2E tests are run.

- **Antares-Simulator** — tracked version in `dependencies.json` (`antares_version`). The version downloaded by CI and used for E2E tests.

## Converter Limitations

The tables below document all PyPSA features not yet supported by the converter. Where a limitation is **enforced**, `_check_converter_limitations()` in `src/pypsa_preprocessor.py` raises a `ValueError` at runtime. Where it is **not enforced**, the feature is silently ignored — the network is accepted but the unsupported data has no effect on the output.

When a new PyPSA version introduces features that the converter does not yet support, add a row to the relevant table. If the limitation is enforced, also add a corresponding assertion in `_check_converter_limitations()`.

### Unsupported Components

| PyPSA Component | Limitation | Enforced |
|---|---|---|
| LineTypes | Supported implicitly — `type` is resolved into `x`/`x_pu` by `calculate_dependent_values()` before conversion | N/A |
| TransformerTypes | Supported implicitly — `type` is resolved into `x_pu_eff` by `calculate_dependent_values()` before conversion | N/A |
| ShuntImpedances | Not used in DC LOPF | No — silently ignored |

### Network-Level Constraints

| Feature | Limitation | Enforced |
|---|---|---|
| Multi-investment periods | Not supported — `network.investment_periods` must be empty | Yes — `ValueError` since 0.0.1 |
| Snapshot weightings ≠ 1 | All snapshot weightings must equal 1.0 | Yes — `ValueError` since 0.0.1 |
| Link multi-port (bus2, bus3, …) | Only 2-port links (bus0 → bus1) supported | No — silently ignored |
| Sub-network AC/DC topology | Not used in DC LOPF | No — silently ignored |

### GlobalConstraints

| Feature | Limitation | Enforced |
|---|---|---|
| Constraint type | Only `primary_energy` type supported | Yes — `ValueError` since 0.0.1 |
| Carrier attribute | Only `co2_emissions` supported | Yes — `ValueError` since 0.0.1 |
| Sense | Only `<=` and `==` supported | Yes — `ValueError` since 0.0.1 |

### Unsupported Parameters per Component

Parameters listed here exist in PyPSA but are not extracted or used by the converter. They are silently ignored unless otherwise noted.

#### Bus

| Parameter | Note |
|---|---|
| `type` | Not used in GEMS model |
| `carrier` | Not used in GEMS model |
| `unit` | Not used in GEMS model |
| `location` | Not used in GEMS model |

#### Generator

Parameters relevant for DC LOPF but not yet implemented:

| Parameter | Note |
|---|---|
| `p_nom_extendable` | Not validated — converter uses `p_nom_min`/`p_nom_max` directly |
| `p_nom_mod` | Modular integer capacity expansion — not implemented |
| `p_set` | Fixed active power output — not implemented |
| `marginal_cost_quadratic` | Quadratic cost term — not supported, enforced to be 0 |
| `committable` | Unit commitment (MILP) — not supported, enforced to be False |
| `start_up_cost` | Unit commitment parameter — not implemented (requires `committable=True` support) |
| `shut_down_cost` | Unit commitment parameter — not implemented (requires `committable=True` support) |
| `stand_by_cost` | Unit commitment parameter — not implemented (requires `committable=True` support) |
| `min_up_time` | Unit commitment parameter — not implemented (requires `committable=True` support) |
| `min_down_time` | Unit commitment parameter — not implemented (requires `committable=True` support) |
| `up_time_before` | Unit commitment parameter — not implemented (requires `committable=True` support) |
| `down_time_before` | Unit commitment parameter — not implemented (requires `committable=True` support) |
| `ramp_limit_start_up` | Unit commitment parameter — not implemented (requires `committable=True` support) |
| `ramp_limit_shut_down` | Unit commitment parameter — not implemented (requires `committable=True` support) |
| `ramp_limit_up` | Inter-temporal ramp constraint — not implemented |
| `ramp_limit_down` | Inter-temporal ramp constraint — not implemented |
| `active` | Only active generators are built — inactive ones are dropped by the converter |

Parameters not relevant for DC LOPF (silently ignored):

| Parameter | Note |
|---|---|
| `control` | AC power flow concept (PQ/PV/Slack) — not applicable to DC LOPF |
| `type` | Placeholder — not implemented even in PyPSA |
| `q_set` | Reactive power set point — no reactive power in DC LOPF |
| `weight` | Used only for network clustering — not optimization |
| `build_year` | Only meaningful with multi-investment periods — already blocked |
| `lifetime` | Only meaningful with multi-investment periods — already blocked |

#### Load

| Parameter | Note |
|---|---|
| `type` | Not used in GEMS model |
| `carrier` | Not used in GEMS model |
| `active` | Only active loads are built — inactive ones are dropped by the converter |

#### Link

Parameters relevant for DC LOPF but not yet implemented:

| Parameter | Note |
|---|---|
| `p_nom_extendable` | Not validated — converter uses `p_nom_min`/`p_nom_max` directly |
| `p_nom_mod` | Modular integer capacity expansion — not implemented |
| `p_set` | Fixed dispatch set point — not implemented |
| `marginal_cost_quadratic` | Quadratic cost term — not implemented |
| `committable` | Unit commitment (MILP) — not implemented |
| `start_up_cost` | Unit commitment parameter — not implemented (requires `committable=True` support) |
| `shut_down_cost` | Unit commitment parameter — not implemented (requires `committable=True` support) |
| `stand_by_cost` | Unit commitment parameter — not implemented (requires `committable=True` support) |
| `min_up_time` | Unit commitment parameter — not implemented (requires `committable=True` support) |
| `min_down_time` | Unit commitment parameter — not implemented (requires `committable=True` support) |
| `up_time_before` | Unit commitment parameter — not implemented (requires `committable=True` support) |
| `down_time_before` | Unit commitment parameter — not implemented (requires `committable=True` support) |
| `ramp_limit_start_up` | Unit commitment parameter — not implemented (requires `committable=True` support) |
| `ramp_limit_shut_down` | Unit commitment parameter — not implemented (requires `committable=True` support) |
| `ramp_limit_up` | Inter-temporal ramp constraint — not implemented |
| `ramp_limit_down` | Inter-temporal ramp constraint — not implemented |
| `bus2`, `bus3`, … / `efficiency2`, `efficiency3`, … | Multi-port sector coupling — only bus0 → bus1 supported |
| `active` | Only active links are built — inactive ones are dropped by the converter |

Parameters not relevant for DC LOPF (silently ignored):

| Parameter | Note |
|---|---|
| `type` | Placeholder — not implemented even in PyPSA |
| `carrier` | Metadata only — not used in optimization constraints |
| `length` | Metadata for pre-computing `capital_cost` — not used in the LP |
| `terrain_factor` | Metadata for pre-computing `capital_cost` — not used in the LP |
| `build_year` | Only meaningful with multi-investment periods — already blocked |
| `lifetime` | Only meaningful with multi-investment periods — already blocked |

#### Line

Parameters relevant for DC LOPF but not yet implemented:

| Parameter | Note |
|---|---|
| `overnight_cost` | Available from PyPSA 1.1.0 — not supported in 1.0.0 |
| `discount_rate` | Available from PyPSA 1.1.0 — not supported in 1.0.0 |
| `fom_cost` | Available from PyPSA 1.1.0 — not supported in 1.0.0 |
| `active` | Only active lines are built — inactive ones are dropped by the converter |
| `build_year` | Only meaningful with multi-investment periods — already blocked |
| `lifetime` | Only meaningful with multi-investment periods — already blocked |

Parameters not relevant for DC LOPF (silently ignored):

| Parameter | Note |
|---|---|
| `type` | Resolved into `x`/`x_pu` by `calculate_dependent_values()` before conversion — not read directly |
| `r` | AC resistive parameter — not used in DC LOPF |
| `g` | AC shunt conductance — not used in DC LOPF |
| `b` | AC susceptance — not used in DC LOPF |
| `s_nom` | Handled by converter: non-extendable → `s_nom_min = s_nom_max = s_nom` |
| `s_nom_extendable` | Handled by converter: `False` → fixed capacity; `True` → use `s_nom_min`/`s_nom_max` |
| `s_nom_set` | Handled by converter: when set → `s_nom_min = s_nom_max = s_nom_set` |
| `length` | Used for type-based reactance scaling — absorbed by `calculate_dependent_values()` |
| `terrain_factor` | Metadata — must be pre-multiplied into `capital_cost` by the user |
| `num_parallel` | Only used when `type` is set — absorbed by `calculate_dependent_values()` |
| `v_ang_min` | Placeholder in PyPSA — not used in optimisation |
| `v_ang_max` | Placeholder in PyPSA — not used in optimisation |

#### Transformer

Parameters relevant for DC LOPF but not yet implemented:

| Parameter | Note |
|---|---|
| `overnight_cost` | Available from PyPSA 1.1.0 — not supported in 1.0.0 |
| `discount_rate` | Available from PyPSA 1.1.0 — not supported in 1.0.0 |
| `fom_cost` | Available from PyPSA 1.1.0 — not supported in 1.0.0 |
| `active` | Only active transformers are built — inactive ones are dropped by the converter |
| `build_year` | Only meaningful with multi-investment periods — already blocked |
| `lifetime` | Only meaningful with multi-investment periods — already blocked |

Parameters not relevant for DC LOPF (silently ignored):

| Parameter | Note |
|---|---|
| `type` | Resolved into `x_pu_eff` by `calculate_dependent_values()` before conversion — not read directly |
| `model` | AC admittance model (t or pi) — not used in DC LOPF |
| `r` | AC resistive parameter — not used in DC LOPF |
| `g` | AC shunt conductance — not used in DC LOPF |
| `b` | AC susceptance — not used in DC LOPF |
| `tap_ratio` | Absorbed into `x_pu_eff` by `calculate_dependent_values()` |
| `tap_side` | AC power flow only — not used in DC LOPF |
| `tap_position` | Only used when `type` is set — absorbed by `calculate_dependent_values()` |
| `phase_shift` | Not used in DC LOPF optimisation |
| `s_nom` | Handled by converter: non-extendable → `s_nom_min = s_nom_max = s_nom` |
| `s_nom_extendable` | Handled by converter: `False` → fixed capacity; `True` → use `s_nom_min`/`s_nom_max` |
| `s_nom_set` | Handled by converter: when set → `s_nom_min = s_nom_max = s_nom_set` |
| `num_parallel` | Only used when `type` is set — absorbed by `calculate_dependent_values()` |
| `v_ang_min` | Placeholder in PyPSA — not used in optimisation |
| `v_ang_max` | Placeholder in PyPSA — not used in optimisation |

#### StorageUnit

Parameters relevant for DC LOPF but not yet implemented:

| Parameter | Note |
|---|---|
| `p_nom_extendable` | Not validated — converter uses `p_nom_min`/`p_nom_max` directly |
| `p_nom_mod` | Modular integer capacity expansion — not implemented |
| `p_dispatch_set` | Fixed active power dispatch set point — not implemented |
| `p_store_set` | Fixed active power charging set point — not implemented |
| `marginal_cost_quadratic` | Quadratic cost term — not supported, enforced to be 0 |
| `sign` | Only `sign = 1` supported — enforced |
| `cyclic_state_of_charge` | Only `True` supported — enforced |
| `state_of_charge_initial` | Initial state of charge — not implemented |
| `state_of_charge_set` | State of charge set points for optimisation snapshots — not implemented |
| `active` | Only active storage units are built — inactive ones are dropped by the converter |

Parameters not relevant for DC LOPF (silently ignored):

| Parameter | Note |
|---|---|
| `control` | AC power flow concept (PQ/PV/Slack) — not applicable to DC LOPF |
| `type` | Placeholder — not implemented even in PyPSA |
| `carrier` | Metadata only — not used in optimization constraints |
| `p_set` | Active power set point for power flow only — not applicable to DC LOPF |
| `q_set` | Reactive power set point — no reactive power in DC LOPF |
| `build_year` | Only meaningful with multi-investment periods — already blocked |
| `lifetime` | Only meaningful with multi-investment periods — already blocked |
| `state_of_charge_initial_per_period` | Only meaningful with multi-investment periods — already blocked |
| `cyclic_state_of_charge_per_period` | Only meaningful with multi-investment periods — already blocked |

#### Store

Parameters relevant for DC LOPF but not yet implemented:

| Parameter | Note |
|---|---|
| `e_nom_extendable` | Not validated — converter uses `e_nom_min`/`e_nom_max` directly |
| `e_nom_mod` | Modular integer capacity expansion — not implemented |
| `e_set` | Fixed energy level set point for optimisation — not implemented |
| `marginal_cost_quadratic` | Quadratic cost term — not supported, enforced to be 0 |
| `sign` | Only `sign = 1` supported — enforced |
| `e_cyclic` | Only `True` supported — enforced |
| `active` | Only active stores are built — inactive ones are dropped by the converter |

Parameters not relevant for DC LOPF (silently ignored):

| Parameter | Note |
|---|---|
| `type` | Placeholder — not implemented even in PyPSA |
| `carrier` | Metadata only — not used in optimization constraints |
| `p_set` | Active power set point for power flow only — not applicable to DC LOPF |
| `q_set` | Reactive power set point — no reactive power in DC LOPF |
| `build_year` | Only meaningful with multi-investment periods — already blocked |
| `lifetime` | Only meaningful with multi-investment periods — already blocked |
| `e_initial` | Irrelevant when `e_cyclic = True` is enforced |
| `e_initial_per_period` | Only meaningful with multi-investment periods — already blocked |
| `e_cyclic_per_period` | Only meaningful with multi-investment periods — already blocked |

## Compatibility Rules

- Patch versions are always backward-compatible within the same Major.Minor.
- Upgrading PyPSA or Antares may require a converter Minor or Major bump — see `CHANGELOG-pypsa_models_library.md` and git history for details.

## Version Files

| Component | Current Version | Version File |
|-----------|----------------|--------------|
| Converter | 0.0.1 | `pyproject.toml` |
| PyPSA Models Library | 1.0.0 | `resources/pypsa_models/pypsa_models.yml` |
| PyPSA | 1.0.0 | `requirements.txt` |
| Antares-Simulator | 9.3.7 | `dependencies.json` |
