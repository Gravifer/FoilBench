# Implementation roadmap

## Phase 1: Python reference

- Typed shared contracts and geometry.
- Stable Fluids, D2Q9 TRT LBM, and blended PIC/FLIP.
- Native ModernGL viewer with direct within-Python warm switching.
- Portable benchmark and fidelity artifacts.

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

