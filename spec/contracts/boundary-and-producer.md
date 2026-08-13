# LBM boundary mapping and producer identity

Status: accepted normative component.

## Phase 3 D2Q9 boundary mapping

Until a scenario-level boundary schema is introduced, Revision 5 freezes the
target mapping for nonperiodic airfoil cases: prescribed velocity at the
lower-x inlet, convective history at the upper-x outlet, freestream equilibrium
on nonperiodic transverse boundaries, interpolated moving-wall bounce-back on
foil cut links, and quadratic sponge profiles. Let
`w=max(3, floor(min(nx,ny)/16))`. Each nonperiodic transverse rim uses width
`w` and maximum blend `0.12`; the downstream outlet uses width `2w` and maximum
blend `0.08`. At integer distance `d` inward from the handled rim, the blend is
`maximum * clamp((width-d)/width,0,1)^2`; overlapping profiles use their
maximum. Poiseuille replaces transverse freestream boundary reconstruction
with channel-wall bounce-back and disables the transverse sponge; the outlet
sponge remains active.

The ordinary-airfoil profile comes from the Python and Julia Phase 2 mapping;
disabling its transverse component at channel walls corrects a shared
Poiseuille inconsistency. TypeScript's former 12%-wide, 0.18 outlet-only
profile is retained only in history, not in the Revision 5 target.

Those constants are contract defaults, not promised live viewer controls.
Future scenario options may override them only in a later revision. Revision 5
conformance includes a downstream-pulse reflection/outlet-history fixture so
equivalent implementations are judged by observable transport as well as
structural labels.

## Producers and execution targets

Artifacts identify `implementation` independently from `execution_target`.
Rust native and Rust/WASM are two targets of one implementation and therefore
do not masquerade as two languages or collide as duplicate cells. A matrix
declares which producer-target pairs are required. Revision 5 requires Rust
native for numerical acceptance and the subset of WASM parity gates fixed by
the acceptance-roster contract.
