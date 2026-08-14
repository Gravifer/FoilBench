# Cross-language fidelity cases

Status: accepted normative component.

Revision 5 replaces implementation-local fidelity parameters with one
schema-validated fixture. Each case fixes precision, domain, resolution,
duration, output interval, initial/control history, measured region, solid
mask treatment, norm, and per-family tolerance. A language may choose stable
internal substeps but may not shorten or coarsen the declared physical run.

The required cases are:

1. uniform periodic flow: component drift, density drift where applicable,
   divergence, and spurious vorticity;
2. Taylor–Green: the declared analytic cell-centred field, velocity L2 error,
   and kinetic-energy decay;
3. planar Poiseuille: analytic profile L2 error and wall leakage, excluding
   explicitly named inlet/outlet guard cells;
4. NACA 0012 at zero geometric angle: symmetry and solid penetration;
5. dynamic NACA 2412 smoke: a cheap 0.05-second finite-state check for wake
   width, recirculation area, enstrophy, and leakage;
6. dynamic NACA 2412 scheduled recovery: the complete 22-second
   4°→14°→25°→4° control history at 32×20, recording transverse wake mixing
   and observed or right-censored recovery without a composite truth or
   visual score.

The scheduled recovery probe samples transverse velocity 1.5 chords
downstream during the second half of the run. `wake_mixing_index` is the
probe RMS normalized by freestream speed and must be finite and nonnegative.
The recovery baseline is the last pre-excursion state, and recovery timing
begins when the schedule returns to 4°. `recovery_observed` is exactly zero or
one. `recovery_elapsed` is the observed duration, or is right-censored at the
four-second observation limit when recovery is not observed. Right-censoring
is a valid result and is not a solver-quality failure.

Analytic formulas, mask dilation, boundary stencil, denominators, and time at
which each measurement is taken belong in the fixture or its owning contract.
Native tests may retain additional small cases, but acceptance evidence must
consume the shared parameters exactly.
