# PyPSA Models Library — Changelog

All notable changes to `resources/pypsa_models/pypsa_models.yml` are documented here.

Versioning follows the rules defined in `COMPATIBILITY.md`:

- **Major** — New PyPSA model added
- **Minor** — Bug fix or improvement to an existing model
- **Patch** — Non-functional change (rename variable/parameter, internal refactor)

---

## [3.0.0] — 2026-11-21

Sector-coupled PyPSA-Eur networks (issue #126). GEMS has no model inheritance yet, so a PyPSA component type
whose options change the equations maps onto several close models; the converter picks one per component.

- **Added** `link_multiport` — Link with up to five buses (`bus0`..`bus4`, `efficiency`..`efficiency4`, static or
  time-varying), also used for the PyPSA `Process` component (`rate0`..`rate4`). The bus0 coefficient is the new
  `rate0` parameter (-1 for links). Unused ports are left unconnected.
- **Added** `generator_ramp`, `link_ramp`, `link_multiport_ramp` — the same models plus PyPSA's ramp limits
  (`ramp_limit_up`, `ramp_limit_down`, static or time-varying, and the first time step ramping from `p_init`). The
  `ramp_up` / `ramp_down` constraints are declared with `out-of-bounds-processing: drop` by the converter.
- **Added** `store_non_cyclic`, `storage_unit_non_cyclic` — `e_cyclic = False` / `cyclic_state_of_charge = False`,
  starting from `e_initial` / `state_of_charge_initial` (balance dropped at the first time step, plus an explicit
  `[0]` initial balance). Their final level reaches global constraints through the shared `is_last_time_step` series.
- **Added** port types `quantity` (time-dependent, summed over the horizon) and `capacity` (time-independent), and
  the ports `operational_port`, `final_level_port`, `capacity_port`, `expansion_volume_port`, `expansion_cost_port`.
- **Added** `global_constraint_co2_min` (primary_energy, `>=`), `global_constraint_quantity_{max,eq,min}`
  (operational_limit, PyPSA-Eur co2_atmosphere) and `global_constraint_capacity_{max,eq,min}`
  (transmission_volume_expansion_limit, transmission_expansion_cost_limit, tech_capacity_expansion_limit, bus
  `nom_min_<carrier>` / `nom_max_<carrier>`). `quota` is now scenario-dependent.
- **Added** `length` to `line` and `link` (transmission volume limits), `sign` to the `store` / `storage_unit`
  power port definitions.
- **Changed** `store`, `storage_unit` — `standing_loss` replaced by `standing_efficiency`, computed by the converter as
  `(1 - standing_loss)^hours_per_time_step`: Antares Modeler rejects non-constant exponents, which time-varying
  snapshot weightings require. Results are unchanged.
- **Changed** `hours_per_time_step` comes from `snapshot_weightings.generators` for generator models and
  `snapshot_weightings.stores` for storage models (they no longer need to be equal), and may vary over time.
- **Fixed** `storage_unit` — charging was bounded by `p_max_pu * p_nom`; PyPSA bounds it by `-p_min_pu * p_nom`.
  Results change for storage units whose `p_min_pu` differs from `-p_max_pu`.
- **Changed** `bus` — `theta_min` / `theta_max` declare their time and scenario dependence (GemsPy >= 0.3.0 makes
  undeclared parameters time- and scenario-dependent).
- **Added** `line_modular`, `transformer_modular` — modular expansion (integer number of `s_nom_mod` modules), moved
  out of `line` and `transformer`.
- **Changed** `line`, `transformer` — continuous capacity only: the integer `n_mod` variable and the `modular` /
  `s_nom_mod` parameters are removed. The integer variable, declared on every line even when fixed to 0, turned every
  study with lines into a MIP: HiGHS then ignores an IPM setting and runs branch-and-bound on dual simplex (8 to 18
  times slower on PyPSA-Eur sector networks). Results are unchanged.

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
