# Scenario semantic constraints

Status: accepted normative component.

JSON Schema establishes the document shape. Scenario construction additionally
enforces the cross-field rules below before a solver or viewer receives the
scenario.

- Bounds are finite and strictly increasing on every declared axis.
- Bounds, resolution, freestream, foil pivot, and periodic-axis names have the
  declared dimension. Periodic axes are unique.
- Control keyframes are finite, nonnegative in time, within the schema angle
  range, and strictly increasing in time.
- `taylor-green` is a two-dimensional initial condition with both `x` and `y`
  periodic. Its velocity is `u=sin(x)cos(y)`, `v=-cos(x)sin(y)` at canonical
  cell centers.
- `poiseuille` is a two-dimensional channel with periodic `x` and nonperiodic
  `y`. Its initial velocity is
  `u=1.5*(1-((y-y_center)/radius)^2)`, `v=0`; the transverse rims are no-slip
  channel walls.
- `freestream` has no additional periodicity constraint.

The shared negative fixture mutates one known-good scenario at a time. Every
language must reject each resulting document during schema validation or
scenario construction, before solver initialization.
