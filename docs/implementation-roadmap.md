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
- All six directed warm swaps, conversion-transient reporting, fresh-state
  fallback, coverage-aware tracer replenishment, a Reynolds circuit breaker,
  and the self-releasing rapid-drag pose-only tier.
- Native `view`, `bench`, `compare`, and `describe` commands; matched fidelity,
  component profiling, portable JSON/CSV/snapshot output, and optional
  chaotic-wake sweep and paired-trajectory experiments.

The automated Julia suite contains 445 passing checks. On the development
machine, the `160 x 96` preview gate measured 19.82 Stable Fluids, 40.41 LBM,
and 14.60 PIC/FLIP solver steps per second after warm-up. Julia remains a peer:
it reads shared specifications and writes shared artifacts but never loads
Python code or Python solvers.

## Phase 2B: TypeScript

Add `implementations/typescript/` with typed-array solvers, a Web Worker, a
Three.js viewer, and browser-native benchmarks. It remains independent of
Python and Julia.

## Phase 3: Rust and WASM

Add `implementations/rust/` after the reference contracts stabilize. One Rust
core supplies native benchmarks and WASM exports. D3Q19 shallow-periodic 3D is
considered only after the 2D implementations pass parity.
