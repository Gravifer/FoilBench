# Solver repertoire contract

Status: accepted normative component of `foilbench-phase2-v1`, revision 3.

This contract defines the minimum numerical content represented by the three
Phase 2 solver identifiers. It prevents an implementation from satisfying the
shared protocol with a qualitatively different algorithm while retaining the
same solver name.

It does not prescribe language-native memory layout, loop organization,
parallelism, linear-solver library, renderer architecture, or optimization.
[The flow solver contract](solver-contract.md) owns the protocol,
[the solver validity contract](solver-validity-contract.md) owns accepted-step
criteria, and [benchmark methodology](../docs/benchmark-methodology.md) owns
measured fidelity and performance.

## Shared Phase 2 repertoire

Every complete Phase 2 language implementation provides these independent
2D solvers:

| Identifier | Family | Required precision modes |
| --- | --- | --- |
| `stable-fluids` | Projected staggered-grid flow | Float32 preview/throughput and Float64 validation |
| `lbm-d2q9` | D2Q9 two-relaxation-time lattice Boltzmann | Float32 preview/throughput and Float64 validation |
| `pic-flip` | Blended particle-in-cell/fluid-implicit-particle | Float32 preview/throughput and Float64 validation |

The implementation advertises only 2D support and rejects thin-3D scenarios
through the capability mechanism. D3Q19 shallow-periodic flow belongs to a
later repertoire revision.

Each family must expose physical velocity through `sample_velocity`, import
and export the shared cell-centered canonical state, respond to runtime
Reynolds changes, and honor authoritative moving-foil control. These common
operations do not erase family-specific private state.

## Stable Fluids

The `stable-fluids` identifier requires:

- a staggered MAC velocity representation;
- pressure projection enforcing the implementation's discrete mass-balance
  constraint;
- viscosity treatment stable for the declared internal timestep, implicit in
  the Phase 2 reference repertoire;
- moving no-slip foil boundary treatment derived from the shared geometry and
  authoritative wall velocity;
- RK2 characteristic backtracing with bilinear interpolation for the
  semi-Lagrangian family of modes;
- limited MacCormack advection as the default transport;
- first-order semi-Lagrangian transport as a selectable comparison mode; and
- the opt-in skew-symmetric explicit-midpoint RK2 transport used by the
  chaotic-wake experiment.

Implementations may choose their pressure/viscosity solver, preconditioner,
stencil organization, and native field layout. A method that merely advects a
cell-centered field without a staggered projection is not this identifier.

## D2Q9 TRT LBM

The `lbm-d2q9` identifier requires:

- the standard nine D2Q9 directions and weights;
- two-relaxation-time collision with distinct symmetric and antisymmetric
  relaxation;
- collide/stream evolution of solver-private populations;
- physical-to-lattice scaling derived from requested Reynolds number and the
  shared lattice Mach limit;
- interpolated moving-wall bounce-back covering both interpolation branches;
- prescribed-velocity inlet, declared transverse freestream or channel-wall
  treatment, a convective outlet retaining prior boundary state, and sponge
  treatment on declared open boundaries;
- deterministic equilibrium initialization of cells uncovered by foil
  movement; and
- canonical import through equilibrium reconstruction with explicit warning
  that source non-equilibrium populations were discarded.

Ping-pong versus in-place streaming, population-major versus cell-major
layout, and scalar versus vectorized collision are language-native choices.
A procedural velocity field or equilibrium-only update without collision and
streaming is not this identifier.

## Blended PIC/FLIP

The `pic-flip` identifier requires:

- solver-private particles distinct from viewer-owned visible tracers;
- a staggered MAC transfer/projection grid shared in meaning with Stable
  Fluids;
- nominal initialization at four particles per fluid cell;
- quadratic B-spline particle/grid transfer weights;
- the conventional cycle: particle-to-grid transfer, grid boundary treatment
  and projection, projected grid-delta construction, blended PIC/FLIP
  grid-to-particle velocity update, then particle advection;
- explicit-midpoint RK2 particle advection;
- a default blend of 95 percent FLIP and 5 percent PIC with live/scenario
  adjustment;
- deterministic population maintenance, reseeding, and SDF collision
  handling, including wall-relative collision response consistent with the
  MAC grid's moving no-slip boundary; and
- canonical import through deterministic solver-particle reseeding followed
  by a PIC-dominant first validation step.

Canonical import and particle reseeding are zero-time reconstruction. They do
not silently advance physical time. The transaction's subsequent validation
advance uses the PIC-dominant blend and consumes exactly its requested
physical interval once.

Transfer reduction order, particle storage structure, threading strategy, and
empty-face implementation are language-native choices provided deterministic
seed and fidelity contracts remain satisfied. Particles used only to draw a
grid velocity field do not constitute this solver family.

## Viewer-owned passive tracers

Visible tracers are deliberately outside the physical solver repertoire.
They consume `sample_velocity` and cannot alter a solver or its benchmark
result. Their shared frozen-field explicit-midpoint integration, lifecycle,
modes, and path continuity are normative in
[the interactive viewer contract](interactive-viewer-contract.md).

This separation means all languages have the same solver repertoire while
also having the same required passive-trajectory mathematics. It prevents
visible tracers from being confused with PIC/FLIP's solver-private particles.

## Fidelity obligations

Claiming a repertoire identifier requires both structural conformance above
and outcome-based evidence. Phase 2 uses the shared cases:

- uniform periodic flow;
- Taylor–Green vortex;
- planar Poiseuille flow;
- NACA 0012 at zero angle; and
- dynamic NACA 2412 with wake and recovery diagnostics.

Scenario meanings and measurement procedure belong to
[benchmark methodology](../docs/benchmark-methodology.md). Thresholds belong
to shared conformance or benchmark fixtures rather than this document. A
finite field, a visually plausible screenshot, or protocol completion alone
does not establish solver-family fidelity.

The accepted pedagogical wake bar remains coherent unsteady separation and an
alternating vortex street. The optional skew-RK2 experiment may demonstrate
irregular multiscale 2D wake behavior without claiming three-dimensional
vortex-stretching turbulence.

## Permitted implementation variation

Cross-language parity is semantic and mathematical, not instruction-for-
instruction identity. Implementations may differ in:

- C-, Fortran-, structure-of-arrays, or array-of-structures native layout;
- iteration and traversal order where deterministic contracts permit it;
- compiled loops, vectorized expressions, SIMD, threading, and GPU kernels;
- pressure/viscosity algorithms satisfying the same validity evidence;
- renderer, event-loop, and snapshot transport; and
- allocation and cache strategies invisible at the artifact boundary.

An optimization that removes a required numerical stage, weakens moving-wall
coupling, changes the requested physical interval, or relies on accepting an
inadmissible finite state is not permitted variation.
