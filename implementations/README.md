# Implementations

Each child is a self-contained language implementation.

- `python/`: Phase 1 canonical reference.
- `julia/`: completed Phase 2A independent implementation and native viewer.
- `typescript/`: reserved for Phase 2B; not scaffolded yet.
- `rust/`: reserved for the Phase 3 native/WASM target; not scaffolded yet.

Implementations share only root-level schemas, scenarios, benchmark matrices,
and artifact meanings. Live solver hosting and warm switching stay within one
language implementation.
