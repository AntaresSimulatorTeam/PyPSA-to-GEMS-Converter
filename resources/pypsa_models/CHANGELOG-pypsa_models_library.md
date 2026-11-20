# PyPSA Models Library — Changelog

All notable changes to `resources/pypsa_models/pypsa_models.yml` are documented here.

Versioning follows the rules defined in `COMPATIBILITY.md`:

- **Major** — New PyPSA model added
- **Minor** — Bug fix or improvement to an existing model
- **Patch** — Non-functional change (rename variable/parameter, internal refactor)

---

## [2.1.0] — 2026-08-22

- **Added** `hours_per_time_step` to `generator`, `storage_unit` and `store` — number of hours represented by one time step (PyPSA `snapshot_weightings.stores` / `.generators`)
- **Added** `objective_weighting` to `generator`, `link`, `storage_unit` and `store` — number of hours a time step represents in the objective (PyPSA `snapshot_weightings.objective`), which may differ from `hours_per_time_step` when operational costs are annualised over representative periods
- **Changed** `generator` — `e_sum_min`/`e_sum_max` constraints and the `emission_port` definition now weight `p` by `hours_per_time_step`
- **Changed** `storage_unit` — `state_of_charge_balance` now applies `(1 - standing_loss)^hours_per_time_step` and weights charge, discharge, inflow and spill by `hours_per_time_step`
- **Changed** `store` — `energy_balance` now applies `(1 - standing_loss)^hours_per_time_step` and weights `p` by `hours_per_time_step`
- **Changed** all `operational_objective` contributions are now weighted by `objective_weighting`

All expressions reduce to the previous ones when both parameters equal 1, so results are unchanged for
hourly studies. Capital cost contributions are deliberately left unweighted, matching PyPSA.

---

## [2.0.1] — 2026-05-21

- **Changed** `emission_factor` to `scenario-dependent: true` for `generator`, `storage_unit`, and `store` — enables per-scenario CO2 emission factors (PyPSA 1.2.0)

---

## [2.0.0] — 2026-05-14

- **Added** `line` model — DC LOPF line with extendable and modular capacity support
- **Added** `transformer` model — DC LOPF transformer with extendable and modular capacity support
- **Added** `theta` variable and `theta_min`/`theta_max` parameters to `bus` model
- **Added** port definition in `bus` model for `angle` field of `flow` port type
- **Changed** `flow` port-type field renamed from `flow` to `power`; `angle` field added
- **Fixed** `storage_unit` model — `spill` variable now correctly bounded by `inflow` parameter

---

## [1.0.0] — 2026-04-19

Initial baseline release.

Supported component models: generators (basic, extendable, p_min/p_max, with emissions),
links (basic, extendable), storage units, stores.

Validated against PyPSA 1.0.0 and Antares-Simulator 9.3.7.
