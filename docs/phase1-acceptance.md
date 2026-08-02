# Phase 1 acceptance

Phase 1 is the typed Python semantic reference. This checklist maps the
original acceptance plan to executable evidence; it does not claim that the
Python implementation is the eventual performance winner.

## Repository and contracts

- The polyglot root separates language-neutral schemas, scenarios, benchmark
  matrices, documentation, and generated artifacts from the Python package.
- Public numerical APIs are typed under strict Pyright. Jaxtyping runtime
  tests reject wrong ranks, swapped axes, inconsistent dimensions, and wrong
  dtypes. Numba kernels remain behind typed wrappers.
- Every `einx` operation used for diagnostics or LBM moments is tested against
  NumPy on multiple shapes.
- PCG32, NACA four-digit geometry, SDF/masks/normals, moving-wall velocity,
  passive display tracers, and solver-private particles are independently
  tested.
- Canonical state uses `z y x component`, a singleton `z` in 2D, declared
  precision, little-endian NPY arrays, named axes, and validated round trips.
  A dimension-aware mid-span extraction API is present for future thin 3D.

## Solvers and interaction

- Stable Fluids uses a MAC grid, midpoint RK2 backtracing, locally limited
  MacCormack or first-order semi-Lagrangian advection, implicit viscosity,
  matrix-free preconditioned projection, and a moving no-slip foil. Opt-in
  direct-face MacCormack and CFL-limited skew-symmetric RK2 transport support
  the accepted chaotic-wake investigation without changing the default path.
- D2Q9 LBM uses TRT collision, compiled collision plus vectorized streaming,
  interpolated moving-wall bounce-back, open boundaries, Mach-limited lattice
  scaling, and deterministic uncovered-cell initialization.
- PIC/FLIP uses four private particles per fluid cell, quadratic B-spline
  transfers, RK2 particle advection, implicit grid viscosity and projection,
  deterministic population maintenance, SDF collisions, and a live blend.
- The Pyglet/ModernGL viewer owns 2,048–8,192 passive tracers and batched path
  histories. A single-owner simulation worker decouples fixed physical steps
  from rendering and reports actual steps/s and simulated/wall time.
- Dragging, pause, reset, solver selection, PIC/FLIP blend, online Reynolds
  control, vorticity, tracer mode, toggleable presentation cropping, and
  diagnostics are supported. The manual GPU smoke procedure is in
  `architecture.md`; headless state/control/worker behavior is automated.
- All six directed warm-swap pairs are tested. State conversion happens only
  at completed steps and preserves controls, time, tracers, and paths. A
  rejected import returns a structured reason and retains the source solver.
  Forced runtime recovery preserves physical time and foil pose, discards
  private flow history, and fully reseeds display tracers without a crossfade.
- Repeated rapid-motion failures temporarily degrade to exact pose updates
  with zero moving-wall angular velocity; normal coupling returns when the
  drag calms. Reynolds instability has a separate reset circuit breaker, and
  Stable Fluids rejects catastrophic CFL or pressure-CG states early enough
  for the viewer worker to recover instead of appearing frozen.

## Benchmarks and fidelity

- `describe`, `view`, `bench`, and `compare` are exposed through the documented
  root-level UV invocation. Benchmark JSON/CSV and optional canonical snapshots
  validate against shared schemas.
- Cold initialization and first-use/JIT time are separated from steady-state
  solver timing. Solver timing excludes rendering, serialization, schema
  validation, wake analysis, and runtime type checking.
- Uniform flow checks velocity drift, density drift, and spurious vorticity.
  Taylor–Green checks analytic velocity error and energy decay. Poiseuille
  checks its profile and wall-normal leakage. NACA 0012 checks symmetry and
  solid penetration. Each canonical case runs all three solvers.
- Dynamic NACA 2412 artifacts report wake width, recirculation, enstrophy,
  normalized transverse mixing, spectral shedding, leakage, and baseline-
  relative recovery without producing a truth or visual-quality score.
- The accepted default pedagogical wake is visibly separated and unsteady,
  including shear-layer roll-up and a coherent alternating vortex street. An
  opt-in scenario adds measured deterministic, irregular 2D wake behavior.
  The vortex street remains sufficient for acceptance; the chaotic extension
  is an overachievement, not a new parity requirement for later languages.
  Phase 1 makes no claim of three-dimensional vortex-stretching turbulence.
- Scenarios and canonical state declare dimensionality and periodic axes. All
  Phase 1 solvers advertise only 2D and reject thin-3D scenarios through the
  common capability mechanism. D3Q19 remains the intended first thin-3D solver.

## Final local evidence

On 2026-07-31, Ruff passed, strict Pyright reported zero errors and warnings,
and all 88 then-current tests passed. The documented CLI workflow then
completed `describe`, schema-validated smoke/compare, the three-repetition
160×96 and 240×144 throughput matrix, all six three-second fixed-stall runs,
and the complete low-resolution dynamic control history. All solver runs
succeeded with finite state and zero reported solid leakage. Exact local
measurements are recorded in `benchmark-results-2026-07-31.md`; generated
artifacts remain gitignored in `results/`.

On 2026-08-01, the accepted chaotic-wake and interaction extension passed all
105 Python tests, Ruff, and strict Pyright with zero errors or warnings.
Targeted checks cover deterministic chaos evidence, online Reynolds behavior,
presentation-only cropping, repeated-failure recovery, pose-only rapid-drag
fallback and release, non-finite pressure rejection, and end-to-end Stable
Fluids recovery. The original full benchmark matrix was not rerun; the
extension's separate timing, refinement, spectrum, and paired-trajectory
evidence is recorded in `chaotic-wake-experiment.md`.

On 2026-08-02, the completed cross-language viewer-contract reconciliation and
high-priority QA pass raised the Python suite to 130 passing tests. Timestamped drag control,
transactional warm-import validation, time-preserving recovery epochs,
explicit tracer generations, typed presentation state, command barriers,
bounded typed failure recovery, hidden-vorticity throttling, honest interactive
throughput under continuous input, prompt LBM/PIC finite-state rejection,
cross-language C/Fortran canonical storage, schedule semantics, and warming
overlays are covered by strict Pyright, Ruff, and pytest.
