# Chaotic-wake experiment contract

Status: accepted normative component of FoilBench contract Revision 4.

This document specifies the optional two-dimensional skew-RK2 chaotic-wake
claim. It defines evidence semantics, not an assertion of three-dimensional
turbulence or quantitative aerodynamic prediction.

## Paired-sensitivity initialization

Every producer constructs paired sensitivity trajectories through the same
observable sequence:

1. Create two fresh `stable-fluids` solvers from the same scenario, seed,
   Reynolds number, precision, resolution, transport mode, and authoritative
   foil pose.
2. Export one canonical base state from the reference solver.
3. Import that unmodified base state back into the reference solver and require
   an accepted import.
4. Add the declared deterministic divergence-free perturbation to a copy of
   the same canonical base state.
5. Import the perturbed state into the second solver through the same canonical
   reconstruction path and require an accepted import.
6. Measure separation only after both imports have succeeded. A rejected
   import, non-finite separation, or zero separation fails the experiment; it
   must not silently leave either solver in its pre-import representation.

This symmetry prevents canonical reconstruction or pressure projection error
from being counted as sensitivity to the requested perturbation. Languages may
use native memory layouts and linear solvers, so post-import values need not be
bit-identical.

The sensitivity artifact records:

- `parameters.epsilon`, the requested maximum perturbation amplitude relative
  to the nondimensional reference speed;
- `metrics.initial_wake_rms_difference`, the realized wake RMS separation
  after both reconstructions;
- both accepted import statuses, the authoritative pose, and the same two
  amplitudes under `initialization`; and
- `initialization.realized_to_requested_ratio`.

The shared case fixture bounds the realized/requested ratio broadly enough to
allow language-appropriate projection differences while rejecting a setup
dominated by reconstruction mismatch. Full-duration evidence must also exceed
the declared minimum amplification and must record a finite, nondecreasing-time
series whose final and maximum values agree with the named summary metrics.

## Mandatory preflight

Before any full-duration paired trajectory, every claimed producer runs the
`initialization_preflight` case from
`spec/conformance/chaotic-wake-cases.json`. The preflight uses the same
full-resolution initialization and reconstruction path, advances only for the
declared short duration, emits a schema-valid sensitivity artifact, and must
pass the shared realized/requested-ratio bounds.

The complete acceptance orchestrator validates the exact required language
roster at preflight before launching any 12-second sensitivity experiment.
Missing, duplicate, rejected, non-finite, zero, out-of-pose, or out-of-envelope
preflights are hard failures.

## Classification scope

Sweep classification continues to require the declared probe RMS, spectral
broadening, broadband fraction, enstrophy variability, and dominant-mode
bounds. Paired sensitivity additionally demonstrates deterministic growth from
the bounded post-reconstruction separation. These are qualitative 2D-flow
criteria. They are not a visual-quality score, an asymptotic Lyapunov exponent,
or evidence of vortex stretching.
