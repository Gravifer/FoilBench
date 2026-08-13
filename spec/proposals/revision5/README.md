# Proposed Revision 5

Status: proposed; not part of the accepted Revision 4 baseline.

Revision 5 is the candidate Phase 3 contract. It extends, rather than edits,
accepted Revision 4 while Rust native and WASM implementations are developed.
Nothing in this directory establishes conformance until the proposal has
executable fixtures, all required producers pass them, and
`spec/contract-version.json` is advanced deliberately.

The proposal resolves the Phase 3 kickoff decisions that Rust must not infer
from Python, Julia, or TypeScript source:

- [geometry semantics](geometry-semantics.md) define the exact NACA model,
  pose, signed-distance surrogate, normals, masks, and moving-wall velocity;
- [canonical state version 2](canonical-state-v2.md) adds a readable geometry
  descriptor, coordinate orientation, and producer/target identity;
- [fidelity cases](fidelity-cases.md) turn the named analytic and airfoil
  cases into one cross-language repertoire;
- [LBM boundaries and producer identity](boundary-and-producer.md) freeze the
  Phase 2 boundary mapping and distinguish native Rust from Rust/WASM.

Permanent policy: pixel-identical or pixel-distance renderer matching is a
won't-do. Viewer conformance concerns observable controls, state, diagnostics,
and semantic overlays; renderer aesthetics remain native to each frontend.

D3Q19 and shallow periodic 3D remain deferred until Stable Fluids, D2Q9 LBM,
and blended PIC/FLIP all pass 2D Rust parity in both the native and intended
WASM execution paths.

