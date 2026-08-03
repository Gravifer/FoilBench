# Flow solver contract

This contract defines the language-neutral behavior of a FoilBench solver.
Implementations are independent and may use native layouts and algorithms.

## Solver identifiers and capabilities

The Phase 2 repertoire is `stable-fluids`, `lbm-d2q9`, and `pic-flip`.
Each solver publishes a display name, supported dimensions, moving-boundary
support, and acceleration description. Phase 2 solvers support only 2D and
must reject 3D scenarios through this capability mechanism.

## Operations

A solver provides these semantic operations:

1. `initialize(scenario, geometry, seed)` creates scenario-time-zero state.
2. `set_reynolds(reynolds)` changes the requested Reynolds number and reports
   an effective value when numerical limits require clamping.
3. `advance(control, target_dt)` consumes the requested physical interval
   using solver-selected stable substeps.
4. `sample_velocity(points)` returns one velocity vector per physical point.
5. `export_state()` returns the canonical cell-centered flow state.
6. `import_state(state, control)` returns a structured accepted or rejected
   outcome; expected incompatibility is not an exception.
7. `diagnostics()` returns finite named scalar values plus warnings.

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
speed, and warnings. A successful step advances by the requested interval
within floating-point tolerance and publishes only finite state. A failed
step must not partially advance physical time or publish partial state.

## Import and failure semantics

Import rejection reasons are `excessive_velocity`, `nonfinite_state`,
`incompatible_geometry`, `incompatible_domain`, `projection_failure`,
`invalid_density`, and `unsupported_conversion`. Accepted imports report
solver-private state that was discarded.

Numerical failures are limited to `excessive_velocity`, `nonfinite_state`,
`projection_failure`, and `invalid_density`. Benchmarks and conformance tests
fail the run. Interactive viewers may use the recovery policy in the viewer
contract, but recovery is outside the solver operation itself.

Canonical import/export preserves dimension, bounds, resolution, periodic
axes, precision, physical time, foil pose, and cell-centered velocity. It does
not preserve pressure history, LBM non-equilibrium populations, PIC/FLIP
deltas, or solver-private particles.
