# Implementation roadmap

## Phase 1: Python reference — complete

**Status:** Completed on 2026-07-31 and enhanced through the accepted
chaotic-wake excursion on 2026-08-01. The requirement-by-requirement evidence
is recorded in [Phase 1 acceptance](phase1-acceptance.md).

- Typed shared contracts and geometry.
- Stable Fluids, D2Q9 TRT LBM, and blended PIC/FLIP.
- Native ModernGL viewer with direct within-Python warm switching.
- Portable benchmark and fidelity artifacts.

The original acceptance audit completed strict Pyright and Ruff checks, all 88
then-current Python tests, finite matched runs for all three solvers at 32 and
48 cells per chord, direct warm-switch coverage, graceful fresh recovery, and
a responsive latest-snapshot viewer. The 32-cells-per-chord preview sustained
double-digit solver updates per second for all three solvers in developed
fixed stall. The accepted extension raises the automated suite to 105 tests
and adds online Reynolds control, diagnostic cropping, rapid-motion recovery,
and fail-fast pressure-solver safety.

The completed 2026-08-02 shared viewer-contract reconciliation and
high-priority QA pass raise the Python suite to 130 tests and align timestamped
drag controls, transactional switching, typed and bounded recovery, tracer
continuity, presentation state, ordered commands, snapshot semantics, and
interactive performance accounting with Julia.

### Accepted wake behavior

The compact Stable Fluids preview produces separated shear-layer roll-up,
recirculation, and an unsteady transverse wake. A coherent alternating vortex
street satisfies the Phase 1 pedagogical requirement. It demonstrates the
onset and persistence of separated unsteady flow without claiming to reproduce
three-dimensional turbulence. That bar is unchanged: later implementations do
not need to reproduce the optional chaotic extension to satisfy basic Phase 1
wake acceptance.

An opt-in skew-symmetric RK2 transport scenario additionally produces a
repeatable, irregular multiscale 2D wake. Probe spectra, enstrophy variation,
resolution comparison, and paired-trajectory sensitivity support describing
it as deterministic numerical 2D chaos. This extension does not replace the
more robust default transport and makes no claim of three-dimensional vortex
stretching or predictive stall fidelity; it is an overachievement beyond the
required coherent-vortex-street behavior.

The benchmark records a downstream transverse-velocity probe, shedding
frequency, Strouhal number, RMS fluctuation, and the fraction of spectral power
in the dominant peak. These values characterize coherent and broadband wakes;
they do not assign either an automated visual-quality score. Subcell boundary
refinement remains a future fidelity improvement, not a Phase 1 blocker.

## Phase 2A: Julia

**Status:** Completed on 2026-08-01 on `codex/phase2a-julia`. Detailed evidence
is recorded in [Phase 2A acceptance](phase2a-acceptance.md).

- Independent Julia package with type-stable contracts, PCG32, NACA geometry,
  x-major shared numerics, canonical state I/O, and schema-valid artifacts.
- Stable Fluids, D2Q9 TRT LBM, and blended PIC/FLIP with runtime Reynolds
  changes, deterministic seeds, requested-time behavior, and explicit 2D
  capabilities.
- Native GLMakie frontend with passive tracers, batched path history,
  vorticity, diagnostic crop, context-sensitive Stable/PIC tuning, all
  Python-equivalent controls, and simulation work isolated from rendering.
- All six directed warm swaps, conversion-transient reporting, structured
  rejection with source retention, time-preserving forced recovery,
  full tracer reseeding with continuity generations, a Reynolds circuit
  breaker, and the self-releasing rapid-drag pose-only tier.
- Native `view`, `bench`, `compare`, and `describe` commands; matched fidelity,
  component profiling, portable JSON/CSV/snapshot output, and optional
  chaotic-wake sweep and paired-trajectory experiments.

The automated Julia suite contains 550 passing checks. On the development
machine, the `160 x 96` preview gate measured 19.82 Stable Fluids, 40.41 LBM,
and 14.60 PIC/FLIP solver steps per second after warm-up. Julia remains a peer:
it reads shared specifications and writes shared artifacts but never loads
Python code or Python solvers.

### Post-reconciliation QA queue

A parallel read-only QA pass on 2026-08-02 found no P0 defect. Its following
high-priority implementation-correctness findings were completed without
requiring additional policy decisions:

- Julia canonical `.npy` storage now agrees with its manifest and is verified
  with a cross-language, nonsymmetric-array fixture;
- Julia scenarios and benchmark results now use the complete shared
  schemas rather than partial hand-written checks;
- Python and Julia pressure/projection breakdowns now become typed numerical
  failures, while Python LBM and PIC/FLIP reject non-finite steps before they
  can return successful reports;
- command-path failures terminate safely, and every accepted warm switch is
  published at its exact completed validation-step boundary before ordinary
  evolution resumes;
- recent failure evidence survives rejected and no-op solver switches;
  and
- interactive `sim/wall` accounting remains active through continuous dragging
  and presentation commands so pacing, publication, tracer, and diagnostic
  costs remain included.

The focused implementation commits are `2b550b4`, `9fda478`, `ffc3bb6`,
`c918572`, `70ffc35`, and `3df6e11`.

Medium-priority agent-actionable follow-up includes making post-import failure
classification reachable, preventing stale diagnostics from leaving warming,
replacing message-based import rejection with dedicated typed outcomes, and
reproducing or ruling out bounded Julia command-channel deadlock under
concurrent producer pressure.

### User-attention items — temporarily deferred

The following policy or experiential work is deliberately deferred until the
user can participate. Agents must not silently settle these choices. They do
not reopen Phase 1 or Phase 2A acceptance:

- choose the fresh-fallback policy after rejected warm import, including
  retry limits, disclosure, telemetry, and pair-specific disablement;
- choose final drag-resolution constants: angular-velocity cap, smoothing
  window, and pose-only hysteresis;
- decide whether diagnostic cadence remains presentation-only or gains a
  separately exposed every-step diagnostic mode;
- design shared language-neutral command transcripts for viewer conformance;
  and
- perform and record a manual cross-platform GPU interaction stress matrix.

The normative minimums and detailed unanswered questions remain recorded in
[the interactive viewer contract](../spec/interactive-viewer-contract.md#open-decisions).
These deferred items should be revisited before claiming full Phase 2B viewer
parity.

## Phase 2B: TypeScript

Add `implementations/typescript/` with typed-array solvers, a Web Worker, a
Three.js viewer, and browser-native benchmarks. It remains independent of
Python and Julia.

## Phase 3: Rust and WASM

Add `implementations/rust/` after the reference contracts stabilize. One Rust
core supplies native benchmarks and WASM exports. D3Q19 shallow-periodic 3D is
considered only after the 2D implementations pass parity.
