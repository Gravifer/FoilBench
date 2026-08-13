# FoilBench Rust workspace

Phase 3 starts with three deliberately separated crates:

- `foilbench-core`: deterministic numerics, typed scenarios, geometry,
  canonical state models, and solver traits; no filesystem or browser APIs;
- `foilbench-native`: native CLI, artifact I/O, schemas, and benchmarks;
- `foilbench-wasm`: coarse `wasm-bindgen` boundary hosted by the existing
  TypeScript simulation worker.

The workspace currently implements the foundation only. It does not advertise
any Rust flow solver. Stable Fluids, D2Q9 TRT LBM, and PIC/FLIP will be added in
that order after the proposed Revision 5 geometry/canonical decisions have
executable parity fixtures.

