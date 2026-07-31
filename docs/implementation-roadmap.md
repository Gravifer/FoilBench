# Implementation roadmap

## Phase 1: Python reference — complete

**Status:** Completed on 2026-07-31. The requirement-by-requirement evidence is
recorded in [Phase 1 acceptance](phase1-acceptance.md).

- Typed shared contracts and geometry.
- Stable Fluids, D2Q9 TRT LBM, and blended PIC/FLIP.
- Native ModernGL viewer with direct within-Python warm switching.
- Portable benchmark and fidelity artifacts.

Phase 1 acceptance was completed with strict Pyright and Ruff checks, all 88
Python tests, finite matched runs for all three solvers at 32 and 48 cells
per chord, direct warm-switch coverage, graceful fresh recovery, and a
responsive latest-snapshot viewer. The 32-cells-per-chord preview sustains
double-digit solver updates per second for all three solvers in developed
fixed stall.

### Accepted wake behavior

The compact Stable Fluids preview produces separated shear-layer roll-up,
recirculation, and an unsteady transverse wake. A coherent alternating vortex
street satisfies the Phase 1 pedagogical requirement. It demonstrates the
onset and persistence of separated unsteady flow without claiming to reproduce
three-dimensional turbulence.

The benchmark records a downstream transverse-velocity probe, shedding
frequency, Strouhal number, RMS fluctuation, and the fraction of spectral power
in the dominant peak. These values characterize coherent and broadband wakes;
they do not assign either an automated visual-quality score. Subcell boundary
refinement and broadband irregularity remain future fidelity improvements, not
Phase 1 blockers.

## Phase 2A: Julia

Add `implementations/julia/` as an independent Julia package. Implement the
same three solvers, a GLMakie viewer, native tests, and BenchmarkTools-based
measurements. Julia reads the shared scenarios and writes the shared result
schema; it does not run inside Python.

## Phase 2B: TypeScript

Add `implementations/typescript/` with typed-array solvers, a Web Worker, a
Three.js viewer, and browser-native benchmarks. It remains independent of
Python and Julia.

## Phase 3: Rust and WASM

Add `implementations/rust/` after the reference contracts stabilize. One Rust
core supplies native benchmarks and WASM exports. D3Q19 shallow-periodic 3D is
considered only after the 2D implementations pass parity.
