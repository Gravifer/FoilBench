# Solver validity contract

Status: accepted normative component of `foilbench-phase2-v1`, revision 3.

This contract defines when a FoilBench numerical operation may be reported as
successful. It applies to ordinary advances, tentative warm-import validation,
and fresh initialization or restart. It supplements the protocol in
[the flow solver contract](solver-contract.md); it does not define viewer
recovery policy, solver-family identity, benchmark scoring, or scientific
prediction accuracy.

The central rule is that **finite is necessary but not sufficient**. A field
can contain only finite numbers while violating a stability envelope, failing
its pressure solve, carrying inadmissible LBM populations, or responding to a
moving boundary in a materially incorrect way.

## Successful-operation invariant

A successful solver operation must:

1. consume finite, supported scenario, control, geometry, and state inputs;
2. complete bounded numerical work without violating the active solver's
   declared admissibility conditions;
3. when advancing, consume exactly the requested physical interval within the
   precision's documented tolerance;
4. leave physical time, control state, solver-private state, and exported
   canonical fields mutually consistent;
5. produce finite report fields and finite exportable arrays;
6. satisfy the solver-family postconditions below; and
7. commit atomically only after every required check succeeds.

Classified numerical failure and rejected import leave the solver at its last
successfully completed state. Physical time, populations, grid fields,
particles, RNG state, and solver-private history roll back together. A caller
must never need to reconstruct whether a classified failure partially mutated
the solver. Fresh initialization or restart has no previous state to restore.
After an exceptional resource failure, process interruption, or broken
internal invariant, the instance is unusable unless it can positively prove
restoration; this stronger exceptional case is not mislabeled as transactional
numerical recovery.

The same invariant applies to the tentative first usable step of a warm
import. The [interactive viewer contract](interactive-viewer-contract.md)
decides what the frontend does after a classified rejection or failed step.

An implementation may enforce a stricter admissibility envelope than the
applicable shared fixture, never a looser one. Until a revision-2 fixture fixes
a threshold, that portion of numerical conformance remains pending rather
than becoming an implementation-selected permanent constant.

## Resolved motion and stability accounting

Solvers must assess all characteristic motion that their discretization
actually resolves. Depending on the family, this includes:

- represented fluid velocity;
- prescribed freestream and inlet velocity;
- moving-wall surface velocity from authoritative `ControlState` angular
  velocity;
- geometry sweep between the previous and requested foil poses; and
- solver-private particle velocity.

Checking only the cell-centered fluid maximum is insufficient during rapid
foil motion. Each explicit term chooses enough internal substeps to respect
its applicable stability condition using the relevant maximum. A method that
is stable for long characteristics still reports and limits characteristic or
boundary displacement when large substep motion would make interpolation,
collision, or geometry resolution untrustworthy. Implicit treatment of one
term does not waive explicit limits belonging to other terms.

The visible pose remains governed by the viewer contract. A solver may reject
motion that it cannot resolve, but it must not silently damp the pose,
reconstruct a different angular velocity, or accept an invalid operation only
because every resulting number remains finite.

The viewer's `pose-only` tier is an explicitly degraded exception. Geometry
may be repositioned while solver-facing angular velocity is zero, so it is not
a physically resolved moving no-slip boundary. Such evolution carries
`motion=pose-only` and `degraded_motion=true`, is excluded from fidelity and
benchmark evidence, and is treated as quasi-static interactive reconstruction
until resolved motion resumes.
The solver still applies finite-state, convergence, and geometric
postconditions; it simply must not present the result as valid evidence of
moving-wall physics.

## Iterative methods

Every iterative solve has a finite iteration bound and reports or internally
records:

- the convergence criterion, norm, and absolute or relative normalization;
- the requested tolerance;
- iterations performed;
- the final residual or equivalent postcondition; and
- whether the criterion was met.

Reaching an iteration limit without meeting the declared acceptance
criterion is a classified numerical failure. Implementations may use
different algorithms and preconditioners, but cannot equate termination with
convergence. A final physical postcondition, such as divergence after
projection, may be stricter than an internal algebraic residual.

For cross-language evidence, an algebraic relative residual means
`||b - A x||_2 / max(||b||_2, epsilon)` with the declared precision-scaled
`epsilon`; an implementation may also report its native convergence norm.
Shared fixtures set the maximum accepted residual and physical postcondition.
An implementation may enforce stricter bounds, never looser ones.

## Common accepted-step evidence

The solver step report or diagnostics associated with its completed revision
must make the following evidence available to conformance and benchmark code:

- requested and advanced physical interval;
- internal substep count;
- maximum represented fluid speed;
- maximum moving-wall or geometry-sweep speed when nonzero;
- the relevant maximum CFL or lattice Mach measure;
- convergence status for any pressure or viscosity solve;
- post-step divergence and solid leakage where those quantities apply; and
- requested and effective Reynolds number when they differ.

An ordinary student overlay need not display every field. Evidence may be
carried in typed reports, diagnostics, or benchmark records, provided it is
attached to the same completed solver revision and cannot be mistaken for a
stale measurement.

Accepted-step diagnostic meanings are:

- `divergence_linf`: the maximum absolute native discrete divergence over
  fluid control volumes, nondimensionalized by `U_ref / c`;
- `solid_leakage`: the maximum absolute wall-relative normal velocity on
  represented fluid-solid faces or cut links, divided by `U_ref`; and
- `maximum_wall_speed`: the maximum represented foil-surface speed divided by
  `U_ref`.

Artifact-level diagnostics reconstructed from canonical cell-centered fields
remain separately defined by benchmark methodology. They must not be confused
with the native accepted-step postconditions above.

For LBM, `solid_leakage` is the native through-flux at represented cut links,
not the wall-normal speed at the adjacent fluid-cell center. Interpolated
bounce-back reflects every population directed through a handled cut link, so
its represented through-flux is zero by construction. Implementations may
report adjacent-cell normal speed under a separate diagnostic name, but must
not relabel it as wall leakage.

For a 2D MAC grid, the shared skew-RK2 advective CFL is
`dt * max(|u_face| / dx + |v_face| / dy)` over the represented fluid region.
The semi-Lagrangian/MacCormack characteristic displacement is
`max(|u| * dt / dx, |v| * dt / dy)` along traced characteristics. Boundary
sweep is the maximum foil-surface displacement over the substep divided by
`min(dx, dy)`. Fixtures define accepted maxima for the selected mode.

## Stable Fluids validity

An accepted `stable-fluids` step must demonstrate that:

- skew-symmetric explicit RK2 advection respected its shared fluid advective
  stability CFL;
- semi-Lagrangian and limited-MacCormack advection reported maximum
  characteristic displacement in grid cells and remained within the shared
  resolution/accuracy envelope;
- foil-boundary sweep remained within its separate cell-scaled resolution
  envelope for every transport mode;
- viscosity and pressure iterations were bounded and met their declared
  convergence criteria;
- post-projection divergence is finite and within the scenario or validation
  tolerance;
- moving solid faces use the authoritative wall velocity and do not retain an
  inadmissible inward normal flow; and
- face fields, sampled cell velocity, time, and control state are finite and
  consistent.

The distinction is deliberate. Semi-Lagrangian backtracing is not subject to
the same advective blow-up condition as explicit Eulerian transport: a
departure point may cross more than one cell without formal CFL instability.
Large displacement can nevertheless cause interpolation diffusion, missed
small-scale structure, limiter-dominated MacCormack updates, and unresolved
solid crossings. It is therefore an accuracy and geometry-resolution limit,
not the skew-RK2 stability CFL. Shared fixtures set both envelopes;
implementations may substep more conservatively.

An implementation may use a matrix-free Krylov method, relaxation, multigrid,
or another language-appropriate solve. The admissibility evidence is shared;
the linear solver is not.

## D2Q9 TRT LBM validity

An accepted `lbm-d2q9` operation must demonstrate that:

- lattice populations and macroscopic fields are finite;
- density is positive and remains within the shared admissible excursion from
  the reference density;
- both TRT relaxation parameters remain in `(0, 2)` in relaxation-frequency
  form, and the Phase 2 magic parameter satisfies
  `(tau_plus - 0.5) * (tau_minus - 0.5) = 3/16`, with
  `tau = 1 / omega`, within precision tolerance;
- the maximum lattice Mach calculation includes fluid, prescribed boundary,
  and moving-wall speeds;
- maximum lattice Mach is at most `0.08`, where
  `Ma = ||u_lattice|| / c_s` and `c_s = 1 / sqrt(3)`;
- a declared physical/lattice time mapping proves that collide-stream steps
  consume the exact requested physical interval;
- any Reynolds or Mach clamping reports the resulting effective Reynolds
  number and reason; and
- boundary reconstruction does not leave invalid populations.

Positivity alone is not a complete density criterion. Likewise, a freestream
Mach cap that ignores a rapidly rotating wall is not a complete lattice Mach
criterion.

One collide-stream operation advances one lattice timestep. A solver may use
an integral number of unchanged lattice timesteps whose mapped sum equals the
request, or establish a new consistent mapping for the operation. Remapping
requires consistent transformation of lattice velocities, viscosity,
relaxation, boundary velocities, and existing non-equilibrium populations.
Changing only the reported time or truncating the final physical interval is
not accepted-time behavior.

## Blended PIC/FLIP validity

An accepted `pic-flip` step must demonstrate that:

- particle positions and velocities, MAC fields, and weights are finite, and
  particle counts are integer and within shared total and per-cell bounds;
- particle-to-grid transfer produces supported faces or invokes the declared
  deterministic empty-face policy;
- grid projection satisfies the same convergence and divergence obligations
  as Stable Fluids;
- grid-to-particle PIC and FLIP updates derive from the completed projected
  grid states;
- particle advection respects a swept-motion envelope based on particle,
  grid, wall-relative, and geometry-sweep velocities; and
- moving-solid collision removes inadmissible inward velocity relative to the
  wall and remains consistent with the projected grid's moving no-slip
  boundary treatment, in addition to repairing geometric penetration.

Projecting a particle outside the foil without treating its velocity relative
to the moving wall is not sufficient moving-boundary coupling. Deterministic
population maintenance may add or remove solver-private particles, but it
must preserve finite state and participate in transactional rollback.
Accepted-step evidence includes empty or underfilled fluid-cell fraction,
unsupported-face fraction, and unresolved-solid-particle count. Endpoint-only
penetration repair is insufficient when either particle or foil can sweep
through more than one resolved cell during a substep.

## Imports and reconstruction

Before reconstruction, every required metadata field and every present array,
including optional density, is validated. Reconstruction then occurs in a
tentative destination. An accepted import must satisfy both canonical-state
requirements and the destination family's admissibility conditions.

Expected rejection uses the structured vocabulary in
[the flow solver contract](solver-contract.md). Normal information loss—such
as discarded LBM non-equilibrium populations or pressure history—is an import
warning, not a validity failure. A reconstruction that is finite but fails
projection, density, motion, or family-specific checks is rejected.

## Failure classification

Anticipated failures must identify the failed condition rather than expose a
broad language exception. They use the reason, stage, and evidence vocabulary
defined by [the flow solver contract](solver-contract.md), without inventing
language-specific reason strings or mapping unrelated failure to projection.

Programming errors, shape violations after validated input, allocation or
resource failures, and broken internal invariants remain exceptional errors.
They are not evidence that Reynolds number or user motion is physically
inadmissible.

## Conformance expectations

Every solver family must be tested for:

- atomic rollback of every mutable state component after a classified failed
  step and a rejected import;
- exact requested-time behavior across solver-selected substeps;
- non-finite input and post-step rejection;
- a finite but deliberately nonconverged iterative solve;
- fluid-speed, characteristic-displacement, moving-wall, and geometry-sweep
  envelope excursions;
- truthful accepted-step evidence attached to the completed revision; and
- recovery-free hard failure in benchmarks and solver conformance tests.

Family-specific suites additionally exercise pressure nonconvergence and
divergence for Stable Fluids, wall-aware Mach and density excursions for LBM,
and particle transfer, projection, collision, and population rollback for
PIC/FLIP.
