# Geometry semantics

Status: accepted normative component.

## Descriptor and coordinates

The Phase 3 geometry family identifier is `naca-four-digit-v1`. Its descriptor
is the JSON object `{family, naca, chord, pivot}`. `naca` is exactly four ASCII
digits, `chord > 0`, and `pivot` contains one finite world coordinate per
dimension. Descriptor equality is semantic field equality after parsing JSON
numbers; implementations must not use source spelling or object key order.

In 2D, local `x` runs from leading edge `0` to trailing edge `c`. At geometric
angle zero, the quarter-chord point `(0.25c, 0)` coincides with the world
`pivot`. Positive geometric angle is counterclockwise. World-to-local first
subtracts the pivot, applies rotation by the negative geometric angle, and
then adds `0.25c` to local `x`.

## NACA four-digit surface

For code digits `MPXX`, use `m=M/100`, `p=P/10`, `t=XX/100`, normalized
coordinate `q=clamp(x/c, 0, 1)`, and the closed-trailing-edge polynomial

`y_t = 5tc(0.2969 sqrt(q) - 0.1260q - 0.3516q² + 0.2843q³ - 0.1036q⁴)`.

For `m>0` and `p>0`, camber is

- `y_c/c = m(2pq-q²)/p²` for `q<p`;
- `y_c/c = m((1-2p)+2pq-q²)/(1-p)²` otherwise.

Camber is zero when `m==0` or `p==0`. The project surface surrogate is
`upper=y_c+y_t`, `lower=y_c-y_t`; it intentionally does not offset thickness
normal to the camber line.

## Signed-distance surrogate and normal

This contract calls the quantity an SDF for API continuity, but it is the
project's vertical-distance surrogate, not exact Euclidean distance to a NACA
curve. For local `(x,y)` within `[0,c]`, return the signed vertical distance to
the nearer surface: nonpositive inside the closed vertical interval and
positive outside. Beyond the leading or trailing edge, combine horizontal
overshoot with the positive vertical distance using `hypot`; a point within
the vertically extended body returns its horizontal overshoot. Containment is
`sdf <= 0`, including the boundary.

Normals use centered differences of this same surrogate in world coordinates
with `epsilon=max(1e-4 c, 1e-6)`, then normalize. If the gradient norm is less
than epsilon, use the rotated local `+y` direction `(-sin(angle), cos(angle))`.
Grid masks evaluate containment at authoritative cell centers.

## Motion evidence and wall velocity

Rigid-wall velocity is `omega × (point-pivot)`, with degrees converted to
radians. The shared conservative represented radius is
`hypot(0.75c, (m+0.51t)c)`. Wall-speed and geometry-sweep evidence must use
that radius; the chord itself is not the rotation radius.

The existing `spec/conformance/naca2412.json` remains the initial executable
fixture. Revision 5 additionally requires NACA 0012 and 2412 cases
covering non-unit chord, transformed pose, leading/trailing-edge queries,
normal fallback, masks, and maximum-radius evidence.
