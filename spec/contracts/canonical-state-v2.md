# Canonical flow state version 2

Status: accepted normative component.

Version 2 preserves Revision 4 array files and semantic axes while adding
identity that version 1 cannot express.

- `geometry` is the complete `naca-four-digit-v1` descriptor defined by the
  geometry contract. Import into a different descriptor must reject with
  `incompatible_geometry` before mutation.
- `producer` separates `implementation` (`python`, `julia`, `typescript`, or
  `rust`) from `execution_target` (`native` or `wasm-browser`). Optional build
  metadata is informative and is not comparison identity.
- velocity component order is `[x, y]` in 2D and `[x, y, z]` in 3D.
- spatial index zero is the cell center nearest the corresponding lower
  bound. Indices increase toward the upper bound. Canonical array axes remain
  `z,y,x,component`, with `z=1` for 2D.

Readers in Phase 3 must support version 1 and version 2. Version-1 states have
unknown geometry identity and may be imported only when the caller supplies
the expected descriptor out of band; they must never be reported as having
passed an intrinsic geometry-identity check. Revision-5 writers emit version
2. Revision-4 artifacts remain readable as version 1 historical inputs.

The descriptor is deliberately readable rather than hashed. A hash could be
added as an integrity cache, but it cannot replace the semantic fields or
become dependent on JSON lexical formatting.
