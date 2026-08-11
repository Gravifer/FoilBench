# Flow solver contract

Status: accepted normative component of `foilbench-phase2-v1`, revision 2.

This contract defines the language-neutral behavior of a FoilBench solver.
Implementations are independent and may use native layouts and algorithms.

Its scope is the solver protocol, identifiers, capabilities, report surface,
and shared import/failure vocabulary. The numerical ingredients behind each
identifier are defined by the
[solver repertoire contract](solver-repertoire-contract.md), while successful
step and import admissibility are defined by the
[solver validity contract](solver-validity-contract.md). Interactive fallback
and presentation behavior belong to the
[interactive viewer contract](interactive-viewer-contract.md).

## Solver identifiers and capabilities

The Phase 2 repertoire is `stable-fluids`, `lbm-d2q9`, and `pic-flip`.
Each solver publishes a display name, supported dimensions, moving-boundary
support, supported precisions, and acceleration description. Phase 2 solvers
support Float32 and Float64 in 2D and must reject 3D scenarios through this
capability mechanism.

## Operations

A solver provides these semantic operations:

1. `initialize(scenario, geometry, seed)` creates scenario-time-zero state and
   is shorthand for `restart` at the scenario's initial pose and Reynolds
   number with zero wall angular velocity.
2. `restart(scenario, geometry, seed, start)` creates fresh private flow state
   at the supplied nonnegative physical time, visible foil pose, requested
   Reynolds number, and zero solver-facing angular velocity. It does not
   reconstruct prior flow history.
3. `set_reynolds(reynolds)` atomically changes the requested Reynolds number
   and returns requested/effective values plus clamping warnings. Rejection
   leaves the previous selection and solver state unchanged.
4. `advance(control, target_dt)` consumes the requested physical interval
   using solver-selected stable substeps.
5. `sample_velocity(points)` returns one velocity vector per physical point.
6. `export_state()` returns the canonical cell-centered flow state.
7. `import_state(state, control)` returns a structured accepted or rejected
   outcome; expected incompatibility is not an exception.
8. `diagnostics()` returns finite named scalar values plus warnings associated
   with a solver-state revision.

The solver-facing `ControlState` contains exactly the requested completion
time, visible foil angle, and authoritative wall angular velocity. Its time
must equal `solver_time + target_dt` within the declared precision tolerance.
At import, canonical-state time and control time must agree. Playback rate,
angle-schedule ownership, pointer gesture state, and presentation choices are
viewer-session state and never enter the solver control type.

`restart` is the operation used by interactive fresh recovery. It avoids
rewriting a scenario, pretending that time is zero, or fabricating a canonical
import merely to create fresh state at the current pose. A viewer constructs a
new solver instance and publishes it only after restart validation succeeds.

An implementation may additionally expose one interactive tuning capability:

```text
id: stable-advection | pic-flip-blend | implementation-defined
label: user-facing short name
value: user-facing current value
can_decrease: boolean
can_increase: boolean
adjust(direction: -1 | +1) -> updated capability state
```

The capability is optional and is not part of benchmark identity. Viewer code
queries it through the solver interface or a typed companion interface rather
than identifying concrete solver classes. Phase 2 Stable Fluids exposes its
transport selection, PIC/FLIP exposes its blend, and LBM exposes no tuning.

`advance` returns requested and advanced time, internal substeps, maximum
speed, solver-state revision, and warnings, together with or linked to the
accepted-step evidence required by the solver validity contract. A successful
step advances by the requested interval within floating-point tolerance and
satisfies that contract; finite state alone is not sufficient. A classified
failed step must atomically restore physical time and all mutable public and
private state rather than publish a partial operation.

## Import and failure semantics

The shared failure reason vocabulary is:

```text
excessive_velocity | stability_limit | nonfinite_state |
convergence_failure | projection_failure | invalid_density |
invalid_population | invalid_relaxation | transfer_failure |
postcondition_failure | time_contract_failure |
incompatible_geometry | incompatible_domain | unsupported_conversion
```

Every anticipated rejection or failed step also carries a stable stage such
as `canonical-import`, `advection`, `viscosity`, `projection`, `boundary`,
`collision`, `streaming`, `particle-transfer`, `particle-advection`,
`population-maintenance`, `time-mapping`, or `postcondition`, plus typed
numeric or boolean evidence relevant to that stage. `projection_failure`
remains the specific pressure-projection outcome; unrelated nonconvergence
uses `convergence_failure` rather than treating projection as a catch-all.

Import additionally uses `incompatible_geometry`, `incompatible_domain`, and
`unsupported_conversion`. Accepted imports report solver-private state that
was discarded. Benchmarks and conformance tests fail on any rejected import
or classified numerical failure. Interactive recovery remains outside the
solver operation itself.

Each solver instance maintains a monotonic state revision. Successful
state-changing operations advance it; rejection and classified failure do
not. Initialization or restart creates revision zero; a successful import,
Reynolds change, tuning mutation, or advance increments it. Step reports and
diagnostics name the revision they describe. A viewer assigns a separate
solver-instance epoch when replacing the active instance; the pair
`(solver_epoch, solver_state_revision)` identifies numerical state without
conflating it with snapshot revisions, command sequences, or recovery epochs.

Canonical import/export preserves dimension, bounds, resolution, periodic
axes, precision, physical time, foil pose, and cell-centered velocity. It does
not preserve pressure history, LBM non-equilibrium populations, PIC/FLIP
deltas, or solver-private particles.
