# PyPSA-to-GEMS Converter — Compatibility Matrix

This table maps converter versions to the PyPSA and Antares-Simulator versions they are compatible with.

| Converter | PyPSA | Antares-Simulator | Notes |
|-----------|-------|-------------------|-------|
| 0.0.1     | 1.2.0 | 9.3.7             | Initial release |
| 0.0.2     | 1.3.0 | 10.1.1            | |
| 0.0.3     | 1.4.0 | 10.1.1            | Upcoming release — all time granularities, sector-coupled PyPSA-Eur networks |

## Versioning Policy

- **Converter** — version in `pyproject.toml` (`[project] version`). Follows semantic versioning:
  - **Major** — Antares-Simulator major version bump
  - **Minor** — Bug fix, new PyPSA feature supported, or PyPSA version update
  - **Patch** — Dependency updates, code optimisation, or PyPSA models library-only change

- **PyPSA Models Library** — version tracked in `resources/pypsa_models/pypsa_models.yml` (`library.version`). Independent versioning:
  - **Major** — New PyPSA model added to `pypsa_models.yml`
  - **Minor** — Bug fix or improvement to an existing model
  - **Patch** — Non-functional change (rename variable/parameter, internal refactor)

- **PyPSA** — tracked version in `pyproject.toml` (`dependencies`, pinned via `pypsa==x.y.z`). The version against which E2E tests are run.

- **Antares-Simulator** — tracked version in `dependencies.json` (`antares_version`). The version downloaded by CI and used for E2E tests.

## Supported PyPSA Features

The converter targets PyPSA's linear optimal power flow (`n.optimize()`), including sector-coupled PyPSA-Eur
networks. A PyPSA component type whose options change the equations maps onto several GEMS models (GEMS has no
model inheritance yet); see the header of `resources/pypsa_models/pypsa_models.yml`.

| Feature | GEMS model(s) / handling |
|---|---|
| Bus, Load (incl. negative or zero `p_set`), Generator, Line, Transformer, Link, StorageUnit, Store | `bus`, `load`, `generator`, `line`, `transformer`, `link`, `storage_unit`, `store` |
| Modular expansion of Lines and Transformers (`s_nom_extendable` with `s_nom_mod > 0`, MILP) | `line_modular`, `transformer_modular` (the integer module count stays out of `line` / `transformer`, so studies without it remain LPs) |
| Multi-port Link (`bus2`..`bus4`, `efficiency2`..`efficiency4`, static or time-varying) | `link_multiport` (unused ports left unconnected) |
| Process (PyPSA ≥ 1.1.0, `rate0`..`rate4`) | `link_multiport` |
| Ramp limits `ramp_limit_up` / `ramp_limit_down` (static or time-varying), `p_init` | `generator_ramp`, `link_ramp`, `link_multiport_ramp`; `out-of-bounds-processing: drop` in `optim-config.yml` |
| Non-cyclic storage (`e_cyclic = False`, `cyclic_state_of_charge = False`) with `e_initial` / `state_of_charge_initial` | `store_non_cyclic`, `storage_unit_non_cyclic`; `out-of-bounds-processing: drop` and an explicit initial balance |
| Snapshot weightings (any positive value, constant or time-varying, columns may differ) | `hours_per_time_step` (`generators` column for generators, `stores` column for storage), `objective_weighting`; a varying column is one shared data series |
| `standing_loss` with any time step duration | `standing_efficiency = (1 - standing_loss)^hours`, computed by the converter |
| `p_nom_set`, `e_nom_set`, `s_nom_set` on extendable components | Capacity pinned: `*_nom_min = *_nom_max = *_nom_set` |
| `overnight_cost`, `discount_rate`, `lifetime`, `fom_cost` | Folded into `capital_cost` (PyPSA's periodized cost) |
| `active = False` | Component dropped (warning), as PyPSA does. A component active in some scenarios only is rejected, as by PyPSA |
| StorageUnit / Store `sign` | `sign * power` on the bus port |
| Infinite fixed capacity (e.g. PyPSA-Eur `co2 atmosphere` store, `e_nom = inf`) | Clamped to `1e11` |
| GlobalConstraint `primary_energy` (`<=`, `==`, `>=`; any single carrier attribute, `co2_emissions` by default) | `global_constraint_co2_{max,eq,min}`: generators, and `(initial - final level)` of non-cyclic stores and storage units |
| GlobalConstraint `operational_limit` | `global_constraint_quantity_{max,eq,min}`: generator energy and non-cyclic storage depletion of the carrier |
| GlobalConstraint `co2_atmosphere` (PyPSA-Eur sector networks) | `global_constraint_quantity_{max,eq,min}`: final level of the non-cyclic stores on buses whose carrier has a non-zero carrier attribute, as PyPSA-Eur's `add_co2_atmosphere_constraint`. **Plain `n.optimize()` ignores this type**: compare with PyPSA run with that `extra_functionality` |
| GlobalConstraint `transmission_volume_expansion_limit`, `transmission_expansion_cost_limit` | `global_constraint_capacity_{max,eq,min}`: `length` or `capital_cost` times the capacity of the extendable lines and links of the listed carriers |
| GlobalConstraint `tech_capacity_expansion_limit` (optionally per `bus`) | `global_constraint_capacity_{max,eq,min}` |
| Bus `nom_min_<carrier>` / `nom_max_<carrier>` (deprecated in PyPSA 1.0) | `global_constraint_capacity_{min,max}`, one per bus and column |
| Scenarios (two-stage stochastic) | Scenario-dependent parameters and global constraint quotas |

## Converter Limitations

The tables below document the PyPSA features not supported by the converter. Where a limitation is **enforced**, the conversion stops with a `ValueError` before any file is written (`check_converter_limitations()` in `src/converter_limitations.py`, which reports every finding in one message). Where it is **not enforced**, the feature is ignored, with a warning when it could surprise the user.

When a new PyPSA version introduces features that the converter does not yet support, add a row to the relevant table. If the feature changes the optimum, also add a check in `src/converter_limitations.py` (compared against PyPSA's default value) and a unit test in `tests/unit_tests/test_converter_limitations.py`.

The **Path** column says what support would take: *variant model* — possible today with a duplicated GEMS model; *GEMS roadmap* — needs a language feature listed on the [GEMS roadmap](https://gems-energy.readthedocs.io/en/latest/home/roadmap/); *open PR* — work in progress in this repository; *non-linear* — out of reach, GEMS is restricted to linear (MI)LP problems.

### Unsupported Components

| PyPSA Component | Limitation | Enforced | Path |
|---|---|---|---|
| LineTypes | Supported implicitly — `type` is resolved into `x`/`x_pu` by `calculate_dependent_values()` before conversion | N/A | — |
| TransformerTypes | Supported implicitly — `type` is resolved into `x_pu_eff` by `calculate_dependent_values()` before conversion | N/A | — |
| ShuntImpedances | Not used in DC LOPF | No — ignored | — |

### Network-Level Constraints

| Feature | Limitation | Enforced | Path |
|---|---|---|---|
| Multi-investment periods | `network.investment_periods` must be empty (`build_year`, `lifetime`, period weightings, growth limits, per-period cyclicity) | Yes — `ValueError` | GEMS roadmap: custom sets and dimensions |
| Links / Processes with `bus5` or more | Only `bus0`..`bus4` are modelled | Yes — `ValueError` | Variant model (more ports); GEMS roadmap: weighted connections |
| Link / Process `delay` (any port) | Delayed delivery | Yes — `ValueError` | Variant model: `p0[t - delay]` in the port field (verified on GemsPy and antares-modeler) when `cyclic_delay = True`; non-cyclic delays need a mask series |
| Piecewise-linear curves | `marginal_cost`, `capital_cost`, `efficiency` given as piecewise curves (PyPSA ≥ 1.3.0) | Yes — by PyPSA: setting the converter's `default` scenario raises `NotImplementedError` | GEMS roadmap: custom sets and dimensions (piecewise-linear costs) |
| Risk preference (CVaR) | Risk-averse stochastic objective (`n.set_risk_preference()`) | Yes — `ValueError` | GEMS roadmap: cross-scenario operators |
| Transformer `phase_shift` ≠ 0, `phase_shift_min < phase_shift_max` | Fixed and optimised phase shifts change the KVL flows | Yes — `ValueError` | Variant model (angle offset in `dc_flow`); see PR #119 (cycle-based KVL) |
| Line / Transformer `v_ang_max` (enforced by PyPSA ≥ 1.4.0) | Angle difference limit | Yes — `ValueError` | Variant model with the angle formulation |
| `transmission_losses` (`n.optimize()` argument) | Not stored in the network | No | Variant model (piecewise-linear loss approximation) |
| `extra_functionality` (e.g. PyPSA-Eur operational reserves, battery charger ratios, renewable targets) | Not stored in the network | No | Case by case |
| Sub-network AC/DC topology | Not used in DC LOPF | No — ignored | — |

### GlobalConstraints

| Feature | Limitation | Enforced | Path |
|---|---|---|---|
| Unknown type (e.g. PyPSA-Eur `Co2Budget`) | Ignored, as by PyPSA | No — warning | — |
| `primary_energy` on several carrier attributes | The GEMS models carry a single `emission_factor` | Yes — `ValueError` | GEMS roadmap: weighted connections |
| `investment_period` set | Investment periods | Yes — `ValueError` | GEMS roadmap: custom sets and dimensions |
| `transmission_expansion_cost_limit` with `fom_cost` on a line or link | The limit weighs capacities by the capital cost without `fom_cost`, the GEMS models carry the periodized cost | Yes — `ValueError` | Variant model (extra parameter) |
| Multi-scenario Benders (Antares-Xpansion) with `primary_energy`, `operational_limit` or `co2_atmosphere` | These constraints sum over all time steps of a scenario: fine in a frontal or per-scenario subproblem, not across decomposed blocks | No | GEMS roadmap: inter-block constraints |

### Unsupported Parameters per Component

#### Generator, Link, Process

| Parameter | Note | Enforced | Path |
|---|---|---|---|
| `marginal_cost_quadratic` | Quadratic cost | Yes — `ValueError` | Non-linear (GEMS is linear); a piecewise-linear approximation needs custom sets |
| `committable` and `start_up_cost`, `shut_down_cost`, `stand_by_cost`, `min_up_time`, `min_down_time`, `up_time_before`, `down_time_before`, `ramp_limit_start_up`, `ramp_limit_shut_down` | Unit commitment (MILP) | Yes — `ValueError` | Open PR #117 (issue #110); GEMS supports integer variables |
| `maintainable`, `maintenance_*` | Maintenance scheduling (MILP, PyPSA ≥ 1.3.0) | Yes — `ValueError` | Variant model (integer variables) |
| `p_nom_mod` on extendable components | Modular expansion (MILP) | Yes — `ValueError` | Variant model, as `line_modular` / `transformer_modular` do |
| `p_set` | Fixed dispatch (`p = p_set` where set) | Yes — `ValueError` | Variant model with a mask series |
| `p_nom_set` on non-extendable components | PyPSA fails on it | Yes — `ValueError` | — |
| `control`, `type`, `q_set`, `weight`, `build_year`, `lifetime` (without `overnight_cost`), `length`, `terrain_factor`, `carrier` | Not used by the LP (`carrier` routes global constraints) | No | — |

#### StorageUnit, Store

| Parameter | Note | Enforced | Path |
|---|---|---|---|
| `marginal_cost_quadratic` | Quadratic cost | Yes — `ValueError` | Non-linear |
| `p_nom_mod` / `e_nom_mod` on extendable components | Modular expansion (MILP) | Yes — `ValueError` | Variant model |
| StorageUnit `p_set`, `p_dispatch_set`, `p_store_set`, `state_of_charge_set`; Store `p_set`, `e_set` | Fixed operation | Yes — `ValueError` | Variant model with a mask series |
| `*_initial_per_period`, `*_cyclic_per_period` | Investment periods | Yes (investment periods) | GEMS roadmap: custom sets and dimensions |
| `control`, `type`, `q_set`, `build_year`, `lifetime` (without `overnight_cost`) | Not used by the LP | No | — |

#### Line, Transformer

| Parameter | Note | Enforced | Path |
|---|---|---|---|
| `s_nom_set` on non-extendable components | PyPSA fails on it | Yes — `ValueError` | — |
| `r`, `g`, `b`, `model`, `tap_side`, `v_ang_min` (deprecated), `terrain_factor`, `num_parallel` / `type` (absorbed by `calculate_dependent_values()`) | AC power flow only or pre-processing | No (`v_ang_min`: warning) | — |

#### Bus, Load

| Parameter | Note | Enforced |
|---|---|---|
| Bus `type`, `carrier` (routes `co2_atmosphere`), `unit`, `location`, `v_mag_pu_*` | Not used by the LP | No |
| Load `type`, `carrier`, `q_set` | Not used by the LP | No |

## Compatibility Rules

- Patch versions are always backward-compatible within the same Major.Minor.
- Upgrading PyPSA or Antares may require a converter Minor or Major bump — see `CHANGELOG-pypsa_models_library.md` and git history for details.

## Version Files

| Component | Current Version | Version File |
|-----------|----------------|--------------|
| Converter | 0.0.3 | `pyproject.toml` |
| PyPSA Models Library | 3.0.0 | `resources/pypsa_models/pypsa_models.yml` |
| PyPSA | 1.4.0 | `pyproject.toml` |
| Antares-Simulator | 10.1.1 | `dependencies.json` |
