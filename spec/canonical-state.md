# Canonical flow state

Status: proposed normative component of `foilbench-phase2-v1`, revision 4.
The file schema remains version 1 because the revision-3 change is semantic,
not structural.

A canonical state is a directory containing `manifest.json`, `velocity.npy`,
and optionally `density.npy`.

`manifest.json` must validate against `canonical-manifest.schema.json`.

`velocity.npy` is a little-endian floating array with semantic axes
`["z", "y", "x", "component"]`. In 2D, `z == 1` and `component == 2`.
`density.npy`, when present, uses `["z", "y", "x"]`.

Canonical velocity is a fluid field. Samples whose cell centers lie inside the
current foil geometry must serialize as zero for every solver family; wall
motion is reconstructed from foil pose and angular velocity rather than from a
solver-specific solid-cell extension. Density values inside the solid are
semantically ignored but must remain finite. Importers must not interpret
zero solid-cell velocity as a stationary-wall command.

The manifest records:

- schema version;
- dimension, bounds, resolution, and periodic axes;
- dtype, storage order, and semantic axis names;
- physical time;
- foil angle and angular velocity;
- source language and solver.

## Foil-angle convention

`angle_degrees` is the geometric world-space rotation of the
leading-edge-to-trailing-edge chord vector: zero aligns it with `+x`, and
positive values rotate it counterclockwise. `angular_velocity_degrees` is the
time derivative of that geometric coordinate. Scenarios, solver controls,
moving-wall velocities, canonical artifacts, and benchmark control histories
all retain this internal convention.

This geometric coordinate is not itself signed aerodynamic angle of attack.
For the Phase 2 airfoil frame, where the leading edge is upstream and the
freestream travels in `+x`, a viewer labeled `AoA` must display
`-angle_degrees`: positive displayed AoA is nose-up, with the leading edge
above the trailing edge. This is a presentation transformation only and must
not be fed back into geometry, solver controls, wall motion, canonical state,
or benchmark identity. A future scenario family with another freestream
direction must define its display transformation explicitly.

Serialization and layout conversion are outside solver benchmark timing.
