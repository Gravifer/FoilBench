# LBM boundary mapping and producer identity

Status: proposed Revision 5 normative component.

## Phase 3 D2Q9 boundary mapping

Until a scenario-level boundary schema is introduced, Revision 5 freezes the
observable Phase 2 mapping for nonperiodic airfoil cases: prescribed velocity
at the lower-x inlet, convective history at the upper-x outlet, freestream
equilibrium on nonperiodic transverse boundaries, interpolated moving-wall
bounce-back on foil cut links, and a quadratic downstream sponge over the
final 12% of x cells with maximum blend 0.18. Poiseuille replaces transverse
freestream boundaries with channel-wall bounce-back.

Those constants are contract defaults, not promised live viewer controls.
Future scenario options may override them only in a later revision. Revision 5
activation needs a downstream-pulse reflection/outlet-history fixture so
equivalent implementations are judged by observable transport as well as
structural labels.

## Producers and execution targets

Artifacts identify `implementation` independently from `execution_target`.
Rust native and Rust/WASM are two targets of one implementation and therefore
do not masquerade as two languages or collide as duplicate cells. A matrix
declares which producer-target pairs are required. Phase 3 initially requires
Rust native for numerical acceptance and a declared subset for WASM parity;
the final roster is fixed only when Revision 5 is activated.

