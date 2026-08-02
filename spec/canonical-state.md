# Canonical flow state

A canonical state is a directory containing `manifest.json`, `velocity.npy`,
and optionally `density.npy`.

`manifest.json` must validate against `canonical-manifest.schema.json`.

`velocity.npy` is a little-endian floating array with semantic axes
`["z", "y", "x", "component"]`. In 2D, `z == 1` and `component == 2`.
`density.npy`, when present, uses `["z", "y", "x"]`.

The manifest records:

- schema version;
- dimension, bounds, resolution, and periodic axes;
- dtype, storage order, and semantic axis names;
- physical time;
- foil angle and angular velocity;
- source language and solver.

Serialization and layout conversion are outside solver benchmark timing.
