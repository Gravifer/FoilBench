# Phase 2A acceptance

Phase 2A was completed on 2026-08-01 as an independent Julia peer to the
Python semantic reference. Julia shares specifications and artifacts with
Python; it does not import Python code or host Python solvers.

## Delivered surface

- Typed solver, report, diagnostic, capability, canonical-state, and result
  contracts with Julia-native x-major storage.
- Stable Fluids with staggered MAC fields, RK2 and limited MacCormack
  transport, matrix-free pressure/viscosity solves, moving-wall boundaries,
  and the opt-in skew-symmetric chaotic transport.
- D2Q9 TRT LBM with interpolated moving-wall bounce-back, open boundaries,
  sponges, relaxation safeguards, deterministic uncovering, and explicit
  requested/effective Reynolds reporting.
- Blended PIC/FLIP with quadratic B-spline transfers, four private particles
  per cell, deterministic maintenance, RK2 advection, SDF collision handling,
  and live blend control.
- A task-isolated GLMakie viewer with all three solvers, foil dragging,
  tracers and path histories, vorticity, cropping, performance overlays,
  Reynolds control, context-sensitive Stable/PIC tuning, inlet tracer
  respawning with staggered display turnover, and all six directed warm swaps.
- Time-preserving fresh recovery at the visible foil pose, deterministic full
  tracer reseeding with continuity generations, the Reynolds circuit breaker,
  and a self-releasing rapid-drag pose-only fallback.
- Structured warm-import outcomes, source retention on rejection, typed
  presentation state, and persistent non-consuming snapshots with revisions.
- Native `view`, `bench`, `compare`, and `describe` commands, schema-valid
  JSON/CSV output, optional canonical snapshots, component benchmarks, and
  deterministic chaotic-wake experiments.

## Automated evidence

The native Julia suite passes 514 checks covering shared RNG and geometry
fixtures, scenarios and canonical states, malformed input, all solver
contracts, runtime Reynolds changes, thin-3D rejection, all six swaps at low
and high attack angles, transactional switch validation, typed and bounded
graceful recovery, ordered command barriers and shutdown, tracer continuity,
hidden-vorticity suppression, and headless viewer behavior.

The matched fidelity repertoire exercises uniform flow, Taylor-Green decay,
Poiseuille flow, NACA 0012 symmetry and penetration, and the dynamic NACA 2412
wake for all three solvers. The combined root verifier runs the strict Python
tooling and the complete Julia suite. On 2026-08-02 it passed Ruff, strict
Pyright with zero errors or warnings, all 123 Python tests, all 514 Julia
checks, and the headless GLMakie environment smoke test.

## Preview performance gate

Run the committed gate with:

```powershell
julia --threads=auto --project=implementations/julia implementations/julia/benchmark/preview_gate.jl
```

At `160 x 96`, after warm-up on the development machine:

| Solver | Solver steps/s |
| --- | ---: |
| Stable Fluids | 19.82 |
| D2Q9 TRT LBM | 40.41 |
| Blended PIC/FLIP | 14.60 |

Profiling first exposed PIC/FLIP at 6.58 steps/s. Replacing allocating scalar
airfoil-distance calls and culling collision queries outside the foil's
bounding radius raised it above the double-digit acceptance gate without
changing its deterministic transfers.

## Presentation and scope

The documented GLMakie GPU smoke procedure is in
[architecture.md](architecture.md#viewer-gpu-smoke-test); automated tests do
not pretend to visually certify a platform OpenGL context.

A coherent alternating vortex street remains sufficient for pedagogical wake
acceptance. Stable Fluids additionally reproduces the optional irregular,
multiscale deterministic 2D chaotic-wake experiment, without claiming
three-dimensional turbulence. All Phase 2A solvers intentionally advertise
2D only. D3Q19 and shallow periodic depth remain deferred until the 2D
Rust/WASM phase reaches parity.
