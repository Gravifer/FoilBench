# Canonical flow state

Status: accepted normative component of `foilbench-phase2-v1`, revision 2.
The file schema remains version 1 because the revision-2 change is semantic,
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

Serialization and layout conversion are outside solver benchmark timing.
