# PyPSA Models Library — Changelog

All notable changes to `resources/pypsa_models/pypsa_models.yml` are documented here.

Versioning follows the rules defined in `COMPATIBILITY.md`:

- **Major** — New PyPSA model added
- **Minor** — Bug fix or improvement to an existing model
- **Patch** — Non-functional change (rename variable/parameter, internal refactor)

---

## [3.0.0] — 2026-09-22

- **Changed** Kirchhoff's Voltage Law from an angle-based to a cycle-flow formulation for `line`/`transformer`, matching how PyPSA's own optimizer implements KVL internally (`pypsa/optimization/constraints.py::define_kirchhoff_voltage_constraints` -- confirmed PyPSA never creates a bus-angle decision variable; it uses the identical cycle-flow method, chosen for sparsity per Hörsch et al. 2018). Motivation: the angle-based approach needed one `theta` variable per bus and one `dc_flow` constraint per branch, every study, whether or not the network had any loops at all -- for a purely radial network (no loops) that's pure overhead, since flows there are already fully pinned down by nodal power balance alone.
- **Removed** `bus.theta` (and its `theta_min`/`theta_max` parameters, and the `flow` port-type's now-unused `angle` field). Removed `line`/`transformer`'s `dc_flow` binding-constraint.
- **Removed** `PyPSAPreprocessor._add_bus_theta_bounds()`/`_buses_with_ac_branches()` (the "slack bus" reference-angle pinning the old formulation needed to avoid an under-determined system of angle equations) -- dead code once no angle variable exists.
- **Added** `kvl_cycle`: a new model with one instance per independent cycle in the Line/Transformer graph, expressing `sum(reactance-weighted, signed branch flow) = 0` for that loop via `sum_connections`.
- **Added** `line`/`transformer.pos_cycle_port`/`neg_cycle_port` (new `kvl_signal` port-type, field `value`): a branch connects whichever of its two ports matches the sign a given loop needs (`+x*p0` or `-x*p0`) to that loop's `kvl_cycle` instance. Two fixed ports are enough for a branch in any number of loops, because a port can fan out to more than one connection at once (confirmed empirically against the real antares-modeler binary: a single port connected to two separate receiving components, and both received the value independently) -- so a branch needing e.g. `+x*p0` in three different loops just connects `pos_cycle_port` to all three.
- **Converter**: `PyPSAStudyConverter._build_kvl_cycle_components()` computes the cycle basis via PyPSA's own `n.cycle_matrix(apply_weights=False)` (not a reimplementation) and generates the `kvl_cycle` components/connections for each converted study.
- Validated against real meshed topologies through the actual antares-modeler binary: `test_lines_triangle` (a genuine triangular loop) matches PyPSA's objective exactly (7250.0) *and* its exact per-line flow split (AC carrying precisely double AB/BC, per the inverse-reactance ratio) -- not just a matching total cost. All existing `line`/`transformer` E2E tests (fixed, LP-extendable, MILP-modular capacity) still pass unchanged.

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
