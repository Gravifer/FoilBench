# Interactive viewer contract

Status: draft for Phase 2A reconciliation and later-language implementations.

This document defines the observable semantics shared by FoilBench interactive
viewers. It intentionally does not require Python's worker design, Julia's
task model, a particular event loop, or a particular renderer. TypeScript and
Rust/WASM implementations should reproduce the behavior described here using
their native concurrency and memory models.

The words **must**, **should**, and **may** distinguish required behavior,
recommended behavior, and permitted variation. Items in [Open decisions](#open-decisions)
are not yet requirements.

## Scope and non-goals

The contract covers:

- user control of foil pose, Reynolds number, solver choice, and presentation;
- the boundary between UI events and solver controls;
- warm switching and interactive numerical failure;
- visible tracers and path continuity;
- asynchronous simulation, snapshots, diagnostics, and overlays.

It does not define solver discretizations, GPU APIs, window-system details,
keyboard layouts, or benchmark fidelity thresholds. It also does not promise
that an arbitrary, discontinuous foil motion has a physically resolved fluid
solution. The viewer must remain responsive and honest when the selected
solver cannot resolve such a motion.

## State domains

An implementation must keep the following concepts distinct, even if its
internal types group them differently.

### Physical flow state

Physical flow state is owned by the active solver. It includes the velocity
and optional density represented by the canonical state, plus solver-private
history such as pressure iterates, LBM populations, or PIC/FLIP particles.
Only the canonical portion crosses solver families.

### Control state

Control state contains the physical time, visible foil angle, solver-facing
foil angular velocity, requested Reynolds number, playback rate, and whether
the scenario's future angle schedule is still active.

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

## Commands, schedules, and ownership

Exactly one simulation owner executes solver mutation. UI and render code
must communicate with it through commands or an equivalently serialized
mechanism. Solver switching, reset, and recovery occur only at completed step
boundaries.

Commands must have a stable ordering. High-frequency pointer poses should be
coalesced to the newest unconsumed pose so an old drag backlog cannot outlive
the gesture. Discrete commands such as reset, pause, solver selection, and
shutdown must not be silently dropped.

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

Pointer samples must carry monotonic timestamps. The simulation owner should
derive angular velocity from recent timestamped pose samples using a short
smoothing window. It must not divide a large pose jump by an arbitrary render
interval, the solver output interval, or a tiny hard-coded minimum event
interval.

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

### Pose-only recovery tier

One rapid-motion failure may receive an ordinary recovery attempt. A
consecutive motion-driven failure may activate `motion=pose-only`:

- the visible foil continues to track the pointer;
- the solver-facing angular velocity is zero;
- the current pose is still enforced in the fresh solver geometry;
- the overlay clearly reports the degraded mode.

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
4. Validate the reconstructed state and its first usable step.
5. Atomically publish the destination, or report a rejected conversion.

The transaction preserves physical time, visible foil pose, requested
Reynolds number, presentation state, visible tracers, and valid path history.
It does not preserve solver-private state that is absent from the canonical
format.

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
control flow. Import should return a typed result equivalent to:

```text
status: accepted | rejected
reason: none
      | excessive_velocity
      | nonfinite_state
      | incompatible_geometry
      | incompatible_domain
      | projection_failure
      | invalid_density
      | unsupported_conversion
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

Plausible rejection cases include non-finite source fields, mismatched domain
metadata, invalid density after LBM reconstruction, excessive wall speed,
failure of the destination projection, or an unsupported dimensional
conversion.

## Numerical failure and fresh recovery

Interactive solver advances must either complete with a finite report or
produce a classified failure promptly. Iterative methods must have finite
input checks and bounded iteration; a frontend must not appear frozen for
tens of seconds before an overflow finally surfaces.

A permitted fresh recovery has these baseline semantics:

- create a new instance of the requested solver at the current physical time;
- preserve the visible foil angle and requested Reynolds number unless the
  Reynolds circuit breaker explicitly resets it;
- initialize with zero solver-facing angular velocity;
- discard imported and solver-private flow history;
- cancel future scheduled angle events and enter manual pose control;
- increment the recovery epoch and record a classified reason;
- clear stale step-rate and solver diagnostics, displaying `warming` or an
  em dash until the first successful step;
- fully reseed visible tracers and invalidate every old path segment;
- never crossfade away the conversion or recovery transient.

The exact policy deciding when a rejected warm import may fall back to fresh
state is not yet settled; see [Open decisions](#open-decisions).

### Reynolds circuit breaker

Repeated failure after an online Reynolds change may restore the scenario's
Reynolds number before one fresh recovery attempt. The overlay must report
the reset. Repeated failure at the scenario Reynolds number must pause rather
than enter an endless restart loop.

Implementations should classify failures and use comparable wall-time windows
and consecutive-failure rules. They must not count an unrelated programming
error as evidence that the selected Reynolds number is unstable.

## Visible tracers and path history

Visible tracers are presentation state, never solver-private particles. Solver
switching therefore does not inherently reseed them.

Two modes are shared:

- **display tracers** use deterministic finite lifetimes and redistribution
  or respawn policies that maintain visually useful coverage;
- **material tracers** preserve residence and depletion behavior and normally
  re-enter only through the inlet.

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

For foil collision, shallow penetration should project the tracer to the
surface along a valid SDF normal. Respawn only when penetration exceeds a
cell-scaled threshold, the normal is non-finite or degenerate, or projection
fails. The exact threshold may scale with the implementation's grid spacing.

### Julia selective-redistribution archive

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

When vorticity is hidden, ordinary evolution should not compute or upload a
new vorticity field. Toggling it on, resetting, switching, or recovering
should request an immediate diagnostic refresh. Ordinary presentation should
use a configurable cadence with a target no slower than approximately 0.1
simulated seconds. A future diagnostic mode may request every-step updates.

Cropping is presentation-only. It changes the visible bounds but not the
solver domain, boundaries, tracer evolution, diagnostics, or canonical state.
Scenarios may separately specify that cropping is available and whether it is
enabled initially.

### Overlay semantics

The overlay should expose at least:

- solver name, physical time, foil angle, and requested Reynolds number;
- effective Reynolds number when different from requested;
- playback rate, solver steps per second, and simulated seconds per wall
  second;
- internal substeps and maximum speed;
- energy, enstrophy, divergence, and solid leakage when available;
- tracer mode, vorticity state, crop state, and solver-specific live tuning;
- pause, recovery, recovery epoch, pose-only, warming, and import-warning
  states.

Rate smoothing is explicitly implementation-specific. Python may retain its
short EMA and Julia may retain instantaneous measurements. Julia should format
volatile numeric fields such as `t`, `step`, `sim/wall`, and `max|u|` to fixed
widths so the overlay does not visibly jitter. Benchmark artifacts remain the
authority for formal median and p95 performance measurements.

After reset, switch, or recovery, old measurements must not be labeled as
current. Show `warming` or `—` until each measurement has been recomputed.

## Snapshot contract

The simulation owner publishes a persistent latest snapshot plus a
monotonically increasing revision. Reading a snapshot must not consume it or
make it unavailable to another reader. A reader may wait for a revision or a
command sequence, but ordinary rendering can always retrieve the current
snapshot.

Published arrays must be detached from mutable solver and tracer storage.
They may be immutable views over stable storage, copied arrays, or ownership-
transferred buffers, provided later solver mutation cannot change a snapshot
already visible to a consumer.

Latest-only publication is allowed and encouraged: slow renderers need not
observe every intermediate step. Revision numbers must still make skipped
snapshots and newly applied commands observable. Careful locking, atomics, or
native channels/conditions may be used; the contract does not prescribe one.

## Conformance expectations

Headless viewer tests should cover:

- non-consuming snapshot reads by multiple consumers;
- monotonically increasing revisions and command acknowledgements;
- event-driven pause, resume, reset, and shutdown;
- coalescing of rapid pose samples without dropping discrete commands;
- the -30 to +30 degree interactive pose limit;
- manual-drag and forced-recovery schedule cancellation;
- schedule preservation across solver and Reynolds changes;
- all directed warm switches, including warnings and classified rejections;
- immediate and post-import failure classification;
- bounded recovery, recovery epochs, and circuit-breaker pause;
- pose-only entry and release;
- full tracer reseeding, generation discontinuities, and shallow collision
  projection;
- hidden-vorticity work suppression and immediate invalidation events;
- cleared metrics and warming state after reset, switch, and recovery;
- unsupported thin-3D capability rejection.

Timing-sensitive tests should use injected clocks or deterministic event
timestamps rather than depending on render-frame timing.

## Existing implementation status

Commits `95a6387` and `778654c` reconcile the Python and Julia viewers with
the normative portions of this draft:

| Area | Implemented shared behavior |
| --- | --- |
| Drag velocity | Timestamped samples, a short smoothing window, a generous nondimensional solver-facing cap, and an unrestricted clamped visible pose. |
| Schedules | Manual drag and forced recovery cancel future angle events; solver and Reynolds changes preserve them; reset restores them. |
| Recovery | Physical time and visible pose are preserved, recovery epochs are reported, metrics return to warming, and solver-private state is declared discarded. |
| Recovery tracers | Both languages perform deterministic full reseeding with explicit continuity generations. |
| Warm-import failure | Both languages return a structured accepted/rejected outcome and retain the source on rejection while fallback remains open. |
| Tracer collision | Shallow penetration projects along a valid SDF normal; deep or invalid cases respawn. |
| Diagnostics | Typed presentation state owns visibility and crop state; hidden vorticity stops field refresh; ordinary cadence targets `0.1 s`. |
| Performance display | Python retains its EMA, Julia retains latest-step values with fixed-width volatile fields, and both show warming placeholders. |
| Simulation owner | Both block while paused and wake for commands or shutdown. |
| Snapshots | Both publish detached, persistent, non-consuming latest snapshots; Julia now adds revisions and command acknowledgements. |

Later implementations should follow these semantics rather than reconstructing
the superseded discrepancies from repository history.

## Open decisions

### Fresh fallback after rejected warm import

The project has not yet chosen one unconditional fallback rule. Before this
section becomes normative, decide:

1. Which rejection reasons permit an automatic fresh destination solver and
   which must leave the source active or pause?
2. Does a post-import first-step failure count as the same transaction, and
   how long is its validation window?
3. How many fresh attempts are allowed before pausing?
4. How prominently must the viewer disclose that the requested switch
   discarded the imported flow state?
5. How are rejected imports and fallback attempts recorded in interactive
   telemetry, benchmark artifacts, and conformance tests?
6. Should repeated successful-looking fallbacks that mask an incompatible
   conversion eventually disable warm import for that solver pair?

Until these questions are settled, implementations must at minimum expose the
structured rejection, avoid infinite restart loops, preserve the user's foil
pose, and make any discarded flow state visible in the overlay.

### Drag resolution parameters

The nondimensional angular-velocity cap, smoothing-window length, and
hysteresis thresholds need matched experiments across Python and Julia. The
observable requirements above apply now; numeric constants should not be
frozen from either implementation's current event-loop accident.

### Diagnostic cadence

Approximately `0.1` simulated seconds is the current presentation target, not
a benchmark invariant. Profiling should determine whether the default and an
optional every-step diagnostic mode need separate scenario settings.
