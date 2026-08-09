# Solver validity contract

Status: normative component of `foilbench-phase2-v1`, revision 2.

This contract defines when a FoilBench numerical operation may be reported as
successful. It applies to ordinary advances, tentative warm-import validation,
and fresh initialization. It supplements the protocol in
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

Failure leaves the solver at its last successfully completed state. Physical
time, populations, grid fields, particles, RNG state, and solver-private
history must roll back together. A caller must never need to reconstruct
whether a nominally failed operation partially mutated the solver.

The same invariant applies to the tentative first usable step of a warm
import. The [interactive viewer contract](interactive-viewer-contract.md)
decides what the frontend does after a classified rejection or failed step.

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
foil motion. Explicit transport chooses enough internal substeps to respect
its documented CFL or lattice-speed envelope using the relevant maximum.
Implicit treatment of one term does not waive explicit limits belonging to
other terms.

The visible pose remains governed by the viewer contract. A solver may reject
motion that it cannot resolve, but it must not silently damp the pose,
reconstruct a different angular velocity, or accept an invalid operation only
because every resulting number remains finite.

## Iterative methods

Every iterative solve has a finite iteration bound and reports or internally
records:

- the convergence criterion and norm;
- the requested tolerance;
- iterations performed;
- the final residual or equivalent postcondition; and
- whether the criterion was met.

Reaching an iteration limit without meeting the declared acceptance
criterion is a classified numerical failure. Implementations may use
different algorithms and preconditioners, but cannot equate termination with
convergence. A final physical postcondition, such as divergence after
projection, may be stricter than an internal algebraic residual.

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

## Stable Fluids validity

An accepted `stable-fluids` step must demonstrate that:

- explicit advection respected the selected transport mode's documented CFL
  bound after accounting for fluid and moving-boundary motion;
- viscosity and pressure iterations were bounded and met their declared
  convergence criteria;
- post-projection divergence is finite and within the scenario or validation
  tolerance;
- moving solid faces use the authoritative wall velocity and do not retain an
  inadmissible inward normal flow; and
- face fields, sampled cell velocity, time, and control state are finite and
  consistent.

An implementation may use a matrix-free Krylov method, relaxation, multigrid,
or another language-appropriate solve. The admissibility evidence is shared;
the linear solver is not.

## D2Q9 TRT LBM validity

An accepted `lbm-d2q9` operation must demonstrate that:

- lattice populations and macroscopic fields are finite;
- density is positive and remains within a documented admissible excursion
  from the reference density;
- both TRT relaxation parameters remain inside their documented stable range;
- the maximum lattice Mach calculation includes fluid, prescribed boundary,
  and moving-wall speeds;
- physical-to-lattice scaling consumes the exact requested physical interval;
- any Reynolds or Mach clamping reports the resulting effective Reynolds
  number and reason; and
- boundary reconstruction does not leave invalid populations or a nonzero
  exported velocity inside solid cells.

Positivity alone is not a complete density criterion. Likewise, a freestream
Mach cap that ignores a rapidly rotating wall is not a complete lattice Mach
criterion.

## Blended PIC/FLIP validity

An accepted `pic-flip` step must demonstrate that:

- particle positions and velocities, MAC fields, weights, and population
  counts are finite;
- particle-to-grid transfer produces supported faces or invokes the declared
  deterministic empty-face policy;
- grid projection satisfies the same convergence and divergence obligations
  as Stable Fluids;
- grid-to-particle PIC and FLIP updates derive from the completed projected
  grid states;
- particle advection respects the particle CFL condition; and
- moving-solid collision removes inadmissible inward velocity relative to the
  wall and applies the solver's declared tangential coupling, in addition to
  repairing geometric penetration.

Projecting a particle outside the foil without treating its velocity relative
to the moving wall is not sufficient moving-boundary coupling. Deterministic
population maintenance may add or remove solver-private particles, but it
must preserve finite state and participate in transactional rollback.

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
broad language exception. Phase 2 retains the shared reasons
`excessive_velocity`, `nonfinite_state`, `projection_failure`, and
`invalid_density`. Reports should add structured stage and evidence fields
without inventing language-specific reason strings.

Programming errors, shape violations after validated input, allocation or
resource failures, and broken internal invariants remain exceptional errors.
They are not evidence that Reynolds number or user motion is physically
inadmissible.

## Conformance expectations

Every solver family must be tested for:

- atomic rollback of every mutable state component after a failed step and a
  rejected import;
- exact requested-time behavior across solver-selected substeps;
- non-finite input and post-step rejection;
- a finite but deliberately nonconverged iterative solve;
- fluid-speed and moving-wall stability-limit excursions;
- truthful accepted-step evidence attached to the completed revision; and
- recovery-free hard failure in benchmarks and solver conformance tests.

Family-specific suites additionally exercise pressure nonconvergence and
divergence for Stable Fluids, wall-aware Mach and density excursions for LBM,
and particle transfer, projection, collision, and population rollback for
PIC/FLIP.
