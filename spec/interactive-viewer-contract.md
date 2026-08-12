# Interactive viewer contract

Status: accepted normative component of `foilbench-phase2-v1`, revision 4.
Exact drag-resolution constants and optional visual-closeness criteria remain
nonblocking follow-up questions; the observable minimums below are accepted.

This document defines the observable semantics shared by FoilBench interactive
viewers. It intentionally does not require Python's worker design, Julia's
task model, a particular event loop, or a particular renderer. TypeScript and
Rust/WASM implementations should reproduce the behavior described here using
their native concurrency and memory models.

The words **must**, **should**, and **may** distinguish required behavior,
recommended behavior, and permitted variation. Items in [Open decisions](#open-decisions)
are not yet requirements.

## Contract-suite scope

This document remains the authority for interactive ownership, commands,
recovery behavior, snapshots, presentation state, visible tracers, and what a
viewer reports to a person. The companion contracts deliberately own adjacent
questions:

- [Flow solver contract](solver-contract.md) defines the language-neutral
  protocol, identifiers, capabilities, and import/failure vocabulary.
- [Solver repertoire contract](solver-repertoire-contract.md) defines the
  numerical ingredients required to claim one of the Phase 2 solver names.
- [Solver validity contract](solver-validity-contract.md) defines when a
  numerical operation is admissible as a successful step or import.

[Canonical state](canonical-state.md) owns serialized field meaning and
[benchmark methodology](../docs/benchmark-methodology.md) owns timed-run and
fidelity measurement. If a subject appears in more than one document, the
document whose scope names it above is authoritative; cross-references in the
other documents are explanatory rather than competing definitions.

## Scope and non-goals

The contract covers:

- user control of foil pose, Reynolds number, solver choice, and presentation;
- the boundary between UI events and solver controls;
- warm switching and interactive numerical failure;
- visible tracers and path continuity;
- asynchronous simulation, snapshots, diagnostics, and overlays.

It does not define solver-family discretizations, accepted-step numerical
criteria, GPU APIs, window-system details, keyboard layouts, or benchmark
fidelity thresholds. It does define the integration and lifecycle of visible
passive tracers because those are viewer-owned numerical presentation state.
It also does not promise that an arbitrary, discontinuous foil motion has a
physically resolved fluid solution. The viewer must remain responsive and
honest when the selected solver cannot resolve such a motion.

## State domains

An implementation must keep the following concepts distinct, even if its
internal types group them differently.

### Physical flow state

Physical flow state is owned by the active solver. It includes the velocity
and optional density represented by the canonical state, plus solver-private
history such as pressure iterates, LBM populations, or PIC/FLIP particles.
Only the canonical portion crosses solver families.

### Control state

Solver-facing `ControlState` contains the requested completion time, visible
foil angle, and authoritative foil angular velocity. Requested Reynolds
number is configured through the solver's atomic Reynolds operation.
Playback rate, pointer-gesture state, and whether future scenario angle events
remain active belong to viewer-session state and never enter `ControlState`.

The visible foil angle and solver-facing angular velocity are deliberately
separate. A viewer may continue to display the pointer-selected pose while
temporarily sending zero or capped angular velocity to a solver that cannot
resolve the measured motion.

### Presentation state

Presentation state contains user-facing choices that are not fluid state,
including:

- vorticity visibility and diagnostic cadence;
- crop state and camera state;
- display or material tracer mode;
- overlay visibility and formatting preferences;
- path-history and tracer generations;
- recovery notices and the recovery epoch.

New implementations should represent this as an explicit typed
`PresentationState` or `ViewerState`, separate from the solver interface and
canonical flow state.

### Time and recovery epoch

The overlay's physical time must mean simulated time, not render time or wall
time. Warm switching and fresh recovery must preserve that physical time.
Only an explicit scenario reset returns it to zero.

Every fresh recovery must increment a monotonically increasing recovery epoch
or counter. A recovery record must state that solver-private history was
discarded and identify why. Consumers must not infer uninterrupted physical
continuity merely because the displayed time was preserved.
Scenario reset does not reset or reuse the recovery epoch; it is a lifetime
counter for the viewer session even though physical time returns to zero.

## Commands, schedules, and ownership

Exactly one simulation owner executes solver mutation. UI and render code
must communicate with it through commands or an equivalently serialized
mechanism. Solver switching, reset, and recovery occur only at completed step
boundaries.

Commands must have a stable ordering. High-frequency pointer poses should be
coalesced to the newest unconsumed pose so an old drag backlog cannot outlive
the gesture. Coalescing must preserve sequence barriers: a pose must not move
from before a release, reset, pause, solver selection, Reynolds command, or
shutdown to after that command. Implementations may coalesce adjacent pose
samples within one such interval, but must retain the newest pose that precedes
each discrete barrier when that pose affects the barrier's meaning. Allocating
a command sequence and making that command visible to the simulation owner
must be one ordered operation even when more than one producer exists.

Discrete commands must not be silently dropped. Shutdown must wake a paused
owner, preserve the ordering of commands accepted before it, and publish or
otherwise expose a final acknowledgement. Commands submitted after shutdown
begins must be rejected explicitly.

While paused, the simulation owner should block until a command, resume, or
shutdown wakes it. It must not poll and republish unchanged state at a fixed
frame rate.

### Scheduled angle events

A scenario angle schedule remains active until manual pose control supersedes
it. The following rules apply:

| Event | Effect on future scheduled angle events |
| --- | --- |
| First mouse-drag pose | Cancel them and enter manual pose control. |
| Forced fresh recovery | Cancel them and freeze the currently visible pose. |
| Solver switch | Preserve them if manual pose control has not superseded them. |
| Reynolds change or reset | Preserve them. |
| Explicit scenario reset (`R`) | Restore the original schedule from time zero. |

Canceling the schedule affects angle events only. It does not reset Reynolds
selection, solver selection, or presentation settings unless the initiating
command explicitly says so.

## Foil dragging

Interactive foil pose must be clamped to **-30 through +30 degrees** in every
native viewer. Scenario-authored controls are separate and may use a wider
range when a scenario explicitly calls for it.

Pointer motion continues to command the geometric chord angle defined in
`canonical-state.md`. The public overlay is derived presentation: with
freestream in `+x`, it must display `AoA = -angle_degrees`, so positive AoA is
nose-up and places the leading edge above the trailing edge. The display
conversion must not alter pointer commands, geometry, solver-facing angular
velocity, scheduled controls, canonical state, or benchmark artifacts.

Pointer poses and timestamps must be finite, and invalid samples must be
rejected before mutating drag, schedule, recovery, or solver state. Pointer
samples must carry monotonic timestamps. The simulation owner should
derive angular velocity from recent timestamped pose samples using a short
smoothing window. It must not divide a large pose jump by an arbitrary render
interval, the solver output interval, or a tiny hard-coded minimum event
interval.

A primary-button press may acquire or capture the pointer, but must not emit a
pose sample, change the foil pose, cancel scheduled angle events, or enter
manual pose control. The first actual pointer-motion event while the button is
held emits the gesture's first pose sample. Consequently, a press and release
without pointer motion is observationally neutral to foil pose and angle
scheduling.

That first moved pose sample establishes pose and time but does not by itself
establish angular velocity. At least two real timestamped motion samples are
required before a nonzero measured angular velocity may be inferred.

The visible pose should follow the user's clamped pointer pose without
solver-specific damping. The solver-facing angular velocity should have a
documented, generous resolution cap. This cap is a numerical resolution
boundary rather than a second visible pose clamp. Its exact value and
smoothing window remain an open parameter, but implementations should choose
comparable thresholds expressed in nondimensional foil-tip speed relative to
freestream speed.

Graceful recovery remains necessary: the angular-velocity cap is not expected
to prevent every numerical failure. Similar motions should enter the same
recovery tier across languages, within the tolerances of their event timing
and solvers.

The solver-facing angular velocity in `ControlState` is authoritative. A
solver may use the visible-angle change to update geometry, but must not
reconstruct a different wall angular velocity from angle delta divided by its
output interval or internal substep. In pose-only mode, every moving-wall and
particle-collision calculation must observe zero angular velocity even while
the visible angle continues changing.

### Pose-only recovery tier

One rapid-motion failure may receive an ordinary recovery attempt. A
consecutive motion-driven failure may activate `motion=pose-only`:

- the visible foil continues to track the pointer;
- the solver-facing angular velocity is zero;
- the current pose is still enforced in the fresh solver geometry;
- the overlay clearly reports the degraded mode.

Pose-only is deliberately not a physically resolved moving-boundary mode:
geometry changes while prescribed wall angular velocity is zero. It is a
quasi-static interactive reconstruction that keeps the visible pose
responsive after repeated failure. Snapshots and telemetry label it
with `motion=pose-only` and `degraded_motion=true`; its steps are excluded from
benchmark, fidelity, and moving-wall conformance evidence.

Pose-only mode must not stick after motion becomes resolvable. Mouse release
followed by a successful stationary step releases it. While the mouse remains
held, sustained gentle motion below a documented hysteresis threshold may
also release it. A failure from the resulting gentle or stationary state must
pause the simulation rather than restart forever.

## Solver switching

Warm switching is a transaction performed at a completed step boundary:

1. Export the source solver's canonical state.
2. Construct and initialize the destination solver without replacing the
   active solver.
3. Import the canonical state and current control into the destination.
4. Tentatively execute and validate the destination's first usable step.
5. Atomically publish the destination, or report a rejected conversion.

The validation step is part of the transaction, not a later recovery window.
Until it succeeds, the source solver and its last published snapshot remain
active and the destination must not be observable as selected. On success,
the destination is published at the physical time completed by that step. On
rejection, the source remains at its previous completed physical time. A
viewer may subsequently apply its separately specified fresh-fallback policy,
but that fallback is not an accepted warm import.

The transaction preserves physical time, visible foil pose, requested
Reynolds number, presentation state, tracer identities, and valid path
history. It does not preserve solver-private state that is absent from the
canonical format.

The accepted destination validation step advances physical time. After it
commits, visible tracers advance exactly once through that same interval using
the completed destination field; their positions, ages, and history therefore
change normally without being reseeded. A rejected transaction leaves every
tracer field unchanged. A later successful fresh fallback preserves time but
performs the separately required full reseed.

Normal lossy reconstruction is not itself a failure. Examples include:

- initializing LBM equilibrium populations without source non-equilibrium
  populations;
- deterministically reseeding PIC/FLIP solver particles;
- discarding a Stable Fluids pressure-solver history.

These losses should appear as import warnings and may create a visible
conversion transient.

### Structured import outcomes

User-induced warm-import rejection is an expected interactive outcome. It may
occur repeatedly during vigorous play and must not use exceptions as routine
control flow. Import returns a typed result using the reason, stage, and
evidence vocabulary owned by the
[flow solver contract](solver-contract.md), equivalent to:

```text
status: accepted | rejected
reason: none | FailureReason
stage: FailureStage | none
evidence: typed numeric/boolean fields
warnings: [loss_of_non_equilibrium, particles_reseeded, ...]
```

Languages may use enums, tagged unions, or result types. Exceptions are
reserved for unexpected programming errors, violated internal invariants,
resource failures, and other conditions the viewer cannot classify.

Implementations must distinguish:

- **immediate rejection**, where canonical validation or reconstruction
  cannot produce an acceptable destination state; and
- **post-import instability**, where reconstruction was accepted but the
  validation step or first ordinary step fails.

Post-import numerical instability should likewise become a structured failed
step or typed recovery signal when it is an anticipated numerical condition.
Here, post-import means instability after the transactional validation step
has succeeded and the destination has been published; failure of that
validation step is a rejected warm-import transaction.

Plausible rejection cases include non-finite source fields, mismatched domain
metadata, invalid density after LBM reconstruction, excessive wall speed,
failure of the destination projection, or an unsupported dimensional
conversion.

All canonical arrays present in an import, including optional density, must be
finite before reconstruction begins. A destination that requires stronger
density bounds may additionally reject them as `invalid_density`.

## Numerical failure and fresh recovery

Interactive solver advances must satisfy the accepted-step requirements in
the [solver validity contract](solver-validity-contract.md) or produce a
classified failure promptly. A finite report is necessary but is not, by
itself, evidence that a step is numerically admissible. Iterative methods must
have finite input checks and bounded iteration; a frontend must not appear
frozen for tens of seconds before an overflow finally surfaces.

Physical time and scheduled pose are committed only after a solver advance
completes successfully. A failed or rejected tentative step must leave them at
the last completed value. Recovery may preserve that value but must not claim
simulated progress for work that failed.

Anticipated numerical failures must use typed results or narrowly scoped
solver exceptions. Broad language exceptions such as `ValueError`,
`ArgumentError`, or `DimensionMismatch` are not numerical classifications by
themselves. Errors in tracers, diagnostics, rendering, command handling, or
other presentation code must not discard a valid solver state or increment a
Reynolds failure counter.

A permitted fresh recovery has these baseline semantics:

- use the solver contract's `restart` operation to create a new instance at
  the current physical time;
- preserve the visible foil angle and requested Reynolds number unless the
  Reynolds circuit breaker explicitly resets it;
- initialize with zero solver-facing angular velocity;
- discard imported and solver-private flow history;
- cancel future scheduled angle events and enter manual pose control;
- increment the recovery epoch and record a classified reason;
- publish the classified recovery reason and stage as structured nullable
  snapshot fields alongside the recovery epoch; status text may repeat them
  for humans but is not the machine-readable record;
- clear stale step-rate and solver diagnostics, displaying `warming` or an
  em dash until the first successful step;
- fully reseed visible tracers and invalidate every old path segment;
- never crossfade away the conversion or recovery transient.

Constructing or restarting a fresh destination is not sufficient evidence
that the fallback succeeded. Before replacing a still-valid source, the fresh
destination must tentatively complete one ordinary requested output interval
under the current pose with zero solver-facing angular velocity. The attempt
is transactional: failure discards the destination and retains the source;
success publishes the destination at the newly completed physical time and
then performs the full tracer reseed at that authoritative pose. Until this
step commits, the destination must not be reported as active or increment the
recovery epoch. A recovery with no valid source applies the same bounded
validation, but pauses if it fails.

The exact policy deciding when a rejected warm import may fall back to fresh
state is:

- `excessive_velocity`, `stability_limit`, `nonfinite_state`,
  `convergence_failure`, `projection_failure`, `invalid_density`,
  `invalid_population`, `transfer_failure`, and `postcondition_failure`
  permit exactly one fresh-destination attempt for the initiating switch
  command;
- `invalid_relaxation`, `time_contract_failure`, `incompatible_geometry`,
  `incompatible_domain`, and `unsupported_conversion` retain the source
  without a fresh attempt;
- a successful fresh destination becomes active, preserves time, visible
  pose, requested Reynolds number, and presentation settings, cancels the
  angle schedule, fully reseeds tracers, increments the recovery epoch, and
  reports `stage=warm-import-fallback`;
- a failed fresh attempt retains the still-valid source and does not retry,
  pause it, or disable that solver pair; and
- benchmarks and solver conformance tests never apply viewer fallback.

### Reynolds circuit breaker

Repeated failure after an online Reynolds change may restore the scenario's
Reynolds number before one fresh recovery attempt. The overlay must report
the reset. Repeated failure at the scenario Reynolds number must pause rather
than enter an endless restart loop.

Implementations should classify failures and use comparable wall-time windows
and consecutive-failure rules. They must not count an unrelated programming
error as evidence that the selected Reynolds number is unstable.
Failure evidence inside the configured window must not be erased merely by
one successful step. A sufficiently long stable interval may allow it to
expire. Likewise, releasing pose-only mode establishes a guarded trial: a
failure on the next gentle or stationary state pauses instead of beginning a
new recovery cycle.

## Visible tracers and path history

Visible tracers are presentation state, never solver-private particles. Solver
switching therefore does not inherently reseed them. Their trajectories are
nevertheless mathematical numerical trajectories: integration order affects
trajectory error, invariant drift, solid encounters, and long-time residence,
not merely appearance.

Two exact contract-level mode identifiers are shared:

- **display tracers** use deterministic finite lifetimes and redistribution
  or respawn policies that maintain visually useful coverage;
- **material tracers** preserve residence and depletion behavior and normally
  re-enter only through the inlet.

An implementation may localize the user-facing labels, but serialized viewer
state and conformance transcripts use `display` and `material`. A legacy or
implementation-local name such as `flow` is not a third mode.

### Passive-advection integration

Ordinary visible-tracer motion uses **frozen-field explicit midpoint** over
each completed physical tracer interval. Let `u_bar_(n+1)(x)` be the active
solver's public velocity sampler after the solver has completed and committed
the interval. The viewer approximates the autonomous path equation
`dx/dt = u_bar_(n+1)(x)` by

```text
k1 = u_bar_(n+1)(x_n)
k2 = u_bar_(n+1)(x_n + 0.5 * dt * k1)
x_(n+1) = x_n + dt * k2
```

Both samples come through the active solver's public velocity-sampling
operation. The `dt` is simulated physical time, not render-frame or wall time.
Playback-rate changes alter the physical interval requested from both solver
and tracers; there is no additional tracer-only velocity multiplier.
Collision and lifecycle handling occur after the midpoint candidate position
is formed.

This is second order for the frozen autonomous field. It is not claimed to be
second-order temporal integration of the evolving equation `dx/dt = u(x,t)`,
because the Phase 2 solver protocol exposes only the newly completed field.
That limitation is shared and testable rather than hidden behind the name
RK2.

Revision 2 selects this method because it removes forward Euler's first-order
curved-trajectory drift with only two public velocity samples, remains
independent of solver-private substeps, and already matches the Python and
Julia reference behavior. A future time-dependent integrator would first
require explicit temporal sampling semantics in the solver protocol.

This requirement applies only to viewer-owned passive tracers. The numerical
motion of solver-private PIC/FLIP particles belongs to the
[solver repertoire contract](solver-repertoire-contract.md).

### Lifecycle and placement

Display-tracer renewal must be deterministic within each language
implementation for the same version, scenario seed, explicit tracer count,
and command transcript. Cross-language tests require the same lifecycle
semantics and statistically comparable coverage, not bit-identical RNG draws,
positions, or paths. Languages may use different PCG32 streams and draw order.

Renewal must be staggered so that a large synchronized cohort does not
disappear in one step. Finite randomized lifetimes are the shared Phase 2
mechanism; their exact bounded distribution may remain implementation-specific.
Initial ages must also be staggered across that distribution instead of
starting every tracer at age zero. Ordinary viewers choose approximately
`256 * domain_area / chord^2` tracers, rounded to an integer and clamped to
2,048 through 8,192; conformance fixtures inject an explicit count.

Every initial placement, inlet respawn, full-domain respawn, scenario reset,
and recovery reseed must produce a finite point inside the domain and outside
the foil at the authoritative current pose. Rejection sampling has a finite
attempt bound and a deterministic valid fallback. A recovery must never test
placement against a hard-coded zero-degree foil.

Discontinuous relocation has a classified reason and placement rule:

| Counter identifier | Display mode | Material mode |
| --- | --- | --- |
| `boundary_exit` | Inlet respawn | Inlet respawn |
| `lifetime_expiry` | Full-domain respawn | Not applicable |
| `invalid_collision` | Full-domain respawn | Inlet respawn |
| `forced_recovery` | Full-domain reseed of all tracers | Full-domain reseed of all tracers |
| `scenario_reset` | Full-domain reseed of all tracers | Full-domain reseed of all tracers |
| `periodic_wrap` | Periodic wrap | Periodic wrap |

Implementations must expose diagnostic counters for these reasons. The
counters are conformance and developer observability, not mandatory content
for the ordinary student overlay. Tracer relocation must not mutate solver
state, physical time, recovery epochs, Reynolds failure evidence, or solver
failure classification.

Counters count relocated tracers, not batches or render frames. They are
monotonic over the viewer session and scenario reset does not reuse them.

Display mode must maintain useful long-time coverage in a controlled uniform
open-flow conformance case. This does **not** require uniform tracer density in
an arbitrary separated or recirculating flow: actively homogenizing every
frame would conceal meaningful residence and wake structure. Coverage is
measured on the full solver domain, independent of presentation cropping, and
the conformance fixture owns the coarse partition, burn-in, duration, and
acceptance threshold.

At most one discontinuous relocation is committed per tracer interval. When
causes overlap, primary-reason precedence is nonperiodic exit, deep or invalid
collision, lifetime expiry, then periodic wrap. Only the committed reason
increments its counter and continuity generation. Shallow projection is
continuous and does not increment either. This ordering makes recycle
telemetry comparable without requiring identical random positions.

Normal boundary exits and ordinary display-tracer turnover may respawn a
single tracer. A forced recovery must use a deterministic full-domain reseed
for all tracers. Although selective replenishment can move fewer particles,
it still creates an obvious discontinuity while leaving the viewer in a
partially old presentation state.

Each tracer must carry a continuity generation. Any teleport, respawn,
reseed, or redistribution increments that generation. A path segment may be
rendered only when both endpoints belong to the same generation. Distance-only
heuristics are insufficient because a physically fast segment and a teleport
can have overlapping lengths.

On a periodic axis, a tracer wraps rather than receiving inlet semantics or a
new material lifetime. Because its displayed coordinate teleports across the
viewport seam, the wrap still increments its continuity generation and no
domain-spanning segment is rendered. Every other respawn, reseed, or
discontinuous relocation likewise resets that tracer's path history at the
new position.

Switching from display to material mode preserves current positions, ages,
generations, and valid history, then disables future lifetime expiry.
Switching from material to display mode preserves positions, generations, and
history while assigning deterministic future expiry deadlines relative to the
switch time; it must not immediately teleport or expire the population.
Explicit scenario reset performs a deterministic full-domain reseed at the
scenario's initial pose, resets lifetime clocks and path history, and preserves
the session-lifetime recovery epoch. Ordinary accepted solver switching
preserves tracer identity and advances the population once as specified
above. Ordinary recycling, reset, and recovery preserve the configured tracer
count. Only a classified fresh recovery applies the recovery reseed rule.

For foil collision, shallow penetration should project the tracer to the
surface along a valid SDF normal. Respawn only when penetration exceeds a
cell-scaled threshold, the normal is non-finite or degenerate, or projection
fails. The exact threshold may scale with the implementation's grid spacing.

### Julia selective-redistribution archive (informative)

The original Phase 2A Julia viewer had a deterministic, coverage-aware
selective redistribution algorithm. It was an interesting implementation
advantage, but observation showed that its visual interruption was not much
smaller than a full reseed. Commit `95a6387` replaced forced-recovery
redistribution with full reseeding.

The archival tag `archive/julia-selective-tracer-redistribution` preserves
commit `f06845b`, the last project state before removal. Commit `3feb00c`
introduced the inlet-coverage and selective-replenishment behavior and remains
the initial historical reference.

## Diagnostics and presentation

Vorticity, cropping, tracers, and overlays are presentation features. They
must not alter solver state or benchmark results.

### Vorticity presentation field

The vorticity underlay is a contracted pedagogical diagnostic, not an
arbitrary renderer effect. Every implementation constructs it from the
cell-centred velocity exported by one completed solver revision and identifies
that source revision in the published snapshot. In 2D its signed raw field is
`omega = dv/dx - du/dy`; derivative stencils may be language-native at the
outer domain edge, but the interior must use centred differences with the
scenario grid spacing.

Before normalization, implementations evaluate the foil mask at the
authoritative pose belonging to that solver revision and set every solid-cell
vorticity value exactly to zero. Solid values, including immersed-boundary
and stair-step outliers, must not enter the display scale. Overwriting the
solid after a scale computed from the full field is nonconforming because it
can visually erase the fluid wake.

For the remaining finite fluid magnitudes, the shared display scale is

```text
scale = max(nearest-rank percentile_99.5(|omega_fluid|),
            0.2 * max(|omega_fluid|),
            1e-6)
omega_display = tanh(omega / scale)
```

The nearest-rank percentile uses zero-based index
`ceil(0.995 * count) - 1`, clamped to the available range. An empty fluid set
uses the floor scale. The published normalized array is finite, has semantic
axes `y x`, lies in `[-1, 1]`, and remains exactly zero inside the current
foil. Index `[0, 0]` is the cell adjacent to the lower-left domain corner:
the first index increases from `y_min` toward `y_max`, and the second from
`x_min` toward `x_max`. Renderers must preserve that world orientation even
when an image or canvas API numbers scanlines from the top; neither vertical
reflection nor sign inversion is permitted. Renderers must not renormalize
the field against their own frame maximum.

Positive vorticity uses the shared warm/rust hue and negative vorticity the
shared blue hue. Near-zero fluid remains visually close to the dark
background; opacity/visibility rises smoothly from magnitude `0.18` toward
full diagnostic strength at `0.9`, with maximum blend/alpha approximately
`0.38`. Exact pixel colour management and interpolation are renderer-native,
but the sign mapping, subtle baseline, and single application of opacity are
observable requirements. A texture alpha and a second material opacity must
not accidentally attenuate the field twice.

The parameters and synthetic outlier cases are frozen in
`spec/conformance/vorticity-display.json`. Tests must include an extreme
solid-cell outlier and an extreme fluid-cell outlier so both masking order and
the maximum-fraction guard are exercised.

When vorticity is hidden, ordinary evolution should not compute or upload a
new vorticity field. It should also avoid repeatedly copying an unchanged full
field into snapshots or renderer observables. Toggling it on, resetting,
switching, or recovering should request an immediate diagnostic refresh.
Ordinary presentation uses a configurable cadence with a target no slower
than approximately 0.1 simulated seconds. Every viewer also exposes an
every-step mode through `D`. The default is cadenced; the choice survives
switch, recovery, and reset, and changing it requests an immediate refresh.
Hidden vorticity remains uncomputed in either mode.

Cropping is presentation-only. It changes the visible bounds but not the
solver domain, boundaries, tracer evolution, diagnostics, or canonical state.
Scenarios may separately specify that cropping is available and whether it is
enabled initially. When no crop is configured, a crop command is a no-op and
the overlay must continue to report the full view.

### Overlay semantics

The overlay should expose at least:

- solver name, physical time, foil angle, and requested Reynolds number;
- effective Reynolds number when different from requested;
- playback rate, solver steps per second, and simulated seconds per wall
  second;
- internal substeps and maximum speed;
- energy, enstrophy, divergence, and solid leakage when available;
- tracer mode, vorticity state, crop state, and solver-specific live tuning;
- diagnostic mode (`cadenced` or `every-step`);
- pause, recovery, recovery epoch, pose-only, warming, and import-warning
  states.

Rate smoothing is explicitly implementation-specific. Python may retain its
short EMA and Julia may retain instantaneous measurements. Julia should format
volatile numeric fields such as `t`, `step`, `sim/wall`, and `max|u|` to fixed
widths so the overlay does not visibly jitter. Benchmark artifacts remain the
authority for formal median and p95 performance measurements.

`solver steps/s` may describe solver-only computation if labeled and measured
consistently. Interactive `sim/wall` means completed simulated time divided by
elapsed monotonic wall time while playback is active; it includes the
simulation owner's tracer, diagnostic, publication, and pacing costs. Paused
time is excluded. A solver-only throughput ratio must use a different label.

After reset, switch, recovery, or any control-plane mutation that advances the
solver-state revision without completing a physical step, old measurements
must not be labeled as current. Reynolds and live-tuning changes while paused
therefore clear rates and solver-derived diagnostics. Show `warming` or `—`
until each measurement has been recomputed by a successful published step.
Import warnings remain visible at least through the first rendered destination
revision and may then be archived in interactive telemetry or history.

## Snapshot contract

The simulation owner publishes a persistent latest snapshot plus a
monotonically increasing revision. Reading a snapshot must not consume it or
make it unavailable to another reader. A reader may wait for a revision or a
command sequence, but ordinary rendering can always retrieve the current
snapshot.

Four identities remain distinct:

- command sequence orders accepted user and lifecycle commands;
- solver epoch changes whenever the active solver instance is replaced;
- solver-state revision changes only after that instance commits numerical
  state; and
- snapshot revision changes whenever newly published numerical, control, or
  presentation state becomes observable.

Recovery epoch independently counts fresh recoveries. Snapshots carry the
latest applicable identities, and solver reports and diagnostics identify the
`(solver_epoch, solver_state_revision)` pair they describe. A presentation-only
command may advance snapshot revision without pretending that a solver step
completed.

Published arrays must be detached from mutable solver and tracer storage.
They may be immutable views over stable storage, copied arrays, or ownership-
transferred buffers, provided later solver mutation cannot change a snapshot
already visible to a consumer.

Latest-only publication is allowed and encouraged: slow renderers need not
observe every intermediate step. Revision numbers must still make skipped
snapshots and newly applied commands observable. Careful locking, atomics, or
native channels/conditions may be used; the contract does not prescribe one.

Small control-plane events such as failure, recovery, pause, and shutdown
must remain publishable when a bulk render frame is waiting for a consumer.
Such an event may replace status fields on an existing physical frame only
when solver epoch, solver-state revision, and recovery epoch identify that
same frame. Status for a newer solver state or recovery is displayed as
pending progress until the matching bulk frame arrives; it must not relabel
old flow, pose, diagnostics, or recovery state as current.
Transports that transfer exclusive ownership of bulk storage require
revision-specific acknowledgements: stale or duplicate acknowledgements must
not release a newer frame, and failed transfer must restore the producer's
ability to publish. Immutable shared snapshots and copied native buffers need
no ownership acknowledgement, but still require bounded latest-only
publication and independent control-plane progress.

Browser implementations continue simulation while hidden, subject to browser
timer throttling, but suppress unnecessary bulk publication. They publish the
latest revision promptly on becoming visible and never execute catch-up steps.
All viewers attempt at most 60 requested output intervals per wall second;
slower solvers expose their actual throughput rather than accumulating debt.

## Interactive solver tuning

Viewer code must not identify concrete solver classes to provide `[` and `]`
controls. A solver may expose one optional typed tuning capability containing
a stable tuning identifier, label, displayed value, and whether adjustment in
either direction is currently available. Adjustment returns the new state.
Solvers without a capability report none; the viewer then displays that no
live tuning is available.

Tuning selections are presentation/session state. They survive switching away
and back and fresh recovery, while scenario reset restores scenario defaults.

## Conformance expectations

Headless viewer tests should cover:

- non-consuming snapshot reads by multiple consumers;
- distinct monotonic command sequences, solver epochs/state revisions,
  snapshot revisions, and recovery epochs;
- event-driven pause, resume, reset, and shutdown;
- coalescing of rapid pose samples without dropping discrete commands;
- ordering barriers around pose, release, reset, pause, switch, and shutdown;
- the -30 to +30 degree interactive pose limit;
- positive displayed AoA for a nose-up geometric pose without changing the
  solver-facing pose or angular velocity;
- press-without-motion neutrality, first-moved-sample drag behavior, and
  authoritative solver-facing angular velocity;
- manual-drag and forced-recovery schedule cancellation;
- schedule preservation across solver and Reynolds changes;
- all directed warm switches, including warnings and classified rejections;
- immediate and post-import failure classification;
- bounded recovery, recovery epochs, and circuit-breaker pause;
- pose-only entry and release;
- full tracer reseeding, generation discontinuities, and shallow collision
  projection;
- frozen-field explicit-midpoint passive trajectories against analytic
  uniform and curved reference fields;
- per-language deterministic staggered display lifetimes, classified recycle
  counters, authoritative-pose placement, and statistically comparable
  long-run display coverage in the shared open-flow fixture;
- mode-switch preservation and exactly-once tracer advancement after accepted
  warm-switch validation;
- periodic wrapping without inlet respawn or seam-crossing path segments;
- isolation of tracer and diagnostic failures from solver recovery;
- hidden-vorticity work suppression and immediate invalidation events;
- cadenced and every-step diagnostic modes;
- revision-specific bulk acknowledgements for ownership-transfer transports
  and independent status events for every transport;
- hidden-browser publication suppression without catch-up;
- cleared metrics and warming state after reset, switch, and recovery;
- unsupported thin-3D capability rejection.

Timing-sensitive tests should use injected clocks or deterministic event
timestamps rather than depending on render-frame timing.

## Phase 2A reconciliation record (informative)

Commits `95a6387` and `778654c` introduced the initial shared design intent. A
two-round blind review then identified violations in both implementations. The
reconciliation sequence through `6383e40` addressed those findings in focused
changes. This table records the resulting baseline; conformance still comes
from observable tests rather than from the existence of particular commits.

| Area | Reconciled shared baseline |
| --- | --- |
| Drag velocity | Timestamped samples, a short smoothing window, a generous nondimensional solver-facing cap, and an unrestricted clamped visible pose. |
| Schedules | Manual drag and forced recovery cancel future angle events; solver and Reynolds changes preserve them; reset restores them. |
| Recovery | Physical time and visible pose are preserved, recovery epochs are reported, metrics return to warming, and solver-private state is declared discarded. |
| Recovery tracers | Both languages perform deterministic full reseeding with explicit continuity generations. |
| Warm-import failure | Both languages return a structured accepted/rejected outcome and retain the source on rejection; revision 2 later settled the bounded fallback policy above. |
| Tracer collision | Shallow penetration projects along a valid SDF normal; deep or invalid cases respawn. |
| Diagnostics | Typed presentation state owns visibility and crop state; hidden vorticity stops field refresh; ordinary cadence targets `0.1 s`. |
| Performance display | Python retains its EMA, Julia retains latest-step values with fixed-width volatile fields, and both show warming placeholders. |
| Simulation owner | Both block while paused and wake for commands or shutdown. |
| Snapshots | Both publish detached, persistent, non-consuming latest snapshots; Julia now adds revisions and command acknowledgements. |

The completed reconciliation covers Python snapshot consumption,
transactional first-step validation, failed-step time atomicity, narrow
numerical-failure classification, bounded baseline-Re recovery, authoritative
pose-only angular velocity, Julia tracer collision shape handling, periodic
tracer continuity, warming overlays, ordered command barriers and shutdown,
honest interactive throughput, and hidden-vorticity copy/upload suppression.

Later implementations should follow the normative semantics and conformance
tests in this document rather than reconstructing the superseded discrepancies
from repository history.

## Open decisions

The decisions below are intentionally deferred until the user can evaluate
their pedagogical and interactive consequences. They are not authorization for
an implementation to choose permanent policy silently. The current minimum
requirements remain binding in the meantime. The implementation defects found
by the post-reconciliation QA pass are not open policy decisions; their
agent-actionable queue and the additional deferred experiential work are
recorded in the
[implementation roadmap](../docs/implementation-roadmap.md#post-reconciliation-qa-queue).

### Drag resolution parameters

The nondimensional angular-velocity cap, smoothing-window length, and
hysteresis thresholds need matched experiments across Python, Julia, and
TypeScript. The
observable requirements above apply now; numeric constants should not be
frozen from either implementation's current event-loop accident.

### Cross-language visual closeness

Phase 2 requires common observable behavior, controls, fidelity cases, and
diagnostic meanings, not pixel-identical renderers. A later user-guided pass
may define visual-closeness criteria. Agents must not turn renderer-specific
color, line, typography, or timing choices into Phase 2 contract failures in
the meantime.
