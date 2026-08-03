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
maintenance pass raise the Python suite to 133 tests and align timestamped
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

The automated Julia suite contains 577 passing checks. On the development
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

The subsequent medium-priority closure pass is also complete:

- the first successful step after reset or recovery refreshes every displayed
  solver diagnostic before leaving `warming`;
- Julia command and wake channels no longer impose bounded backpressure while
  coordination locks are held, with a four-producer regression crossing the
  former queue bound;
- solver import APIs in both languages return accepted or rejected
  `ImportOutcome` values directly for expected incompatibility and numerical
  reconstruction failures rather than classifying exception messages; and
- the first ordinary step after a published warm switch retains an explicit
  post-import marker, which is cleared only by a successful step and produces
  `stage=post-import` recovery telemetry on failure.

The focused closure commits are `4e49007`, `eaf60f9`, `c65c2bf`, and
`d80974b`.

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

**Status:** Automated implementation and acceptance revalidated on 2026-08-03
on `codex/phase2b-typescript`; final interactive-policy acceptance remains
open.

Phase 2B begins by formalizing the shared solver, scenario, benchmark-matrix,
canonical-manifest, result, and PCG32 contracts. It then adds an independent
strict TypeScript implementation with typed-array Stable Fluids, D2Q9 TRT
LBM, and blended PIC/FLIP solvers; a Web Worker simulation owner; a Three.js
viewer; and Chromium-native benchmarks. TypeScript shares artifacts and
semantic fixtures with Python and Julia but never loads their code or solvers.

Completed implementation milestones are:

1. shared contract hardening and TypeScript conformance foundations;
2. Stable Fluids and the worker/Three.js viewer;
3. D2Q9 TRT LBM and blended PIC/FLIP;
4. all directed warm swaps at low and high attack angles, graceful recovery, online Reynolds control,
   tracer continuity, and presentation parity;
5. Chromium benchmark artifacts and offline three-language comparison; and
6. strict TypeScript, cross-language fidelity, browser interaction, and
   `160 x 96` double-digit warmed-step acceptance.

The implementation supplies all three independent typed-array solvers,
MacCormack/semi-Lagrangian/skew-RK2 Stable Fluids transport, TRT LBM with
moving-wall and open-domain treatment, four-particles-per-cell blended
PIC/FLIP, a latest-only Web Worker owner, Three.js paths and vorticity,
transactional switching, classified recovery, canonical snapshots, and a
Chromium-native benchmark runner. The matched validation suite, production
build, browser interaction smoke test, and snapshot interoperability checks
pass. Python successfully loads the TypeScript C-order canonical artifacts.

An extensive parallel QA pass after the initial acceptance record found that
several tests and implementation shortcuts were weaker than those claims. The
resulting corrective sequence is complete:

- canonical imports validate full metadata and array payloads and roll back
  atomically on failure (`da7feb8`);
- LBM uses exact requested physical intervals, a true D2Q9 Mach cap, adaptive
  temporal scaling, both interpolated bounce-back branches, channel-wall
  reflection, and zeroed solid-cell canonical velocity (`3ba149d`, `0314c7e`);
- PIC/FLIP now performs the conventional particle-to-MAC transfer, projection,
  PIC/FLIP update, and RK2 particle advection cycle with deterministic
  occupancy maintenance and transactional rollback (`09288e8`);
- the Web Worker coalesces pointer poses, permits only one transferable
  snapshot in flight, publishes latest-only revisions, isolates presentation
  failures, and reports owner-loop rather than solver-only timing (`7773965`);
- quantitative Poiseuille and NACA 0012 checks, all viewer-level warm-swap
  directions, wake spectra, and excursion recovery evidence replace the
  earlier superficial coverage (`0314c7e`, `a408952`); and
- benchmark artifacts now carry a self-contained matched-run identity across
  all three languages (`d304a52`).

The revalidated TypeScript suite contains 72 passing Vitest checks plus the
live Chromium smoke test. The shared Julia suite contains 577 passing checks
after adopting the expanded result contract.

The reproducible `preview-gate.json` matrix passed three repetitions for every
solver at `160 x 96`. Observed median step ranges on the development machine
were `18.0–20.9 ms` for Stable Fluids, `78.1–85.8 ms` for LBM, and
`80.9–84.2 ms` for PIC/FLIP after the conventional solver corrections. See
[Phase 2B acceptance](phase2b-acceptance.md) for commands and evidence.

A coherent alternating vortex street remains sufficient wake behavior. The
skew-RK2 chaotic-wake sweep and paired-trajectory experiment are optional
post-acceptance enhancements.

### Phase 2B user-policy gate

Core work uses the current normative minimums in the interactive viewer
contract. Before Phase 2B is marked complete, the user must evaluate and the
three viewers must reconcile:

- fresh fallback after rejected warm import, including eligible reasons,
  retries, disclosure, telemetry, and pair disablement;
- final drag cap, smoothing window, and pose-only hysteresis;
- whether to expose an every-step diagnostic mode;
- language-neutral viewer command transcripts; and
- the manual cross-platform GPU interaction matrix.

These remain decisions, not implementation latitude. Chromium is the required
Phase 2B browser; Firefox and Safari are compatibility follow-ups.

Consequently, the engineering work is complete but Phase 2B must not be
relabeled unconditionally **Completed** until the user evaluates this gate.

## Phase 3: Rust and WASM

Add `implementations/rust/` after the reference contracts stabilize. One Rust
core supplies native benchmarks and WASM exports. D3Q19 shallow-periodic 3D is
considered only after the 2D implementations pass parity.
