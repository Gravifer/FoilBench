# Implementations

Each child is a self-contained language implementation.

- `python/`: Phase 1 canonical reference.
- `julia/`: completed Phase 2A independent implementation and native viewer.
- `typescript/`: completed Phase 2B strict TypeScript implementation, native
  viewer, browser gate, and Revision 4 conformance peer.
- `rust/`: active Phase 3 workspace with a platform-neutral core, native CLI,
  and WASM boundary. The foundation is scaffolded; no Rust solver is yet
  advertised.

Implementations share only root-level schemas, scenarios, benchmark matrices,
and artifact meanings. Live solver hosting and warm switching stay within one
language implementation.
