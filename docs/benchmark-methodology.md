# Benchmark methodology

The latest local Phase 1 interactive snapshot is recorded in
[benchmark-results-2026-07-31.md](benchmark-results-2026-07-31.md). Julia emits
the same result-schema fields and canonical snapshot semantics for offline
cross-language comparison.

Runs are matched by physical domain, resolution, Reynolds number, control
history, precision, seed, and simulated duration. Each solver may choose the
stable internal timestep and number of substeps it needs.

Every result JSON repeats that identity directly: benchmark-matrix and
scenario IDs, bounds and periodic axes, resolution, Reynolds number,
freestream, foil specification, control history, requested and actually
simulated duration, output interval, precision, and seed. Comparisons therefore
do not have to infer physical equivalence from a filename or scenario ID.

The runner records cold initialization and the first cold solver step
separately from steady-state work. Initialization-time compilation is therefore
visible in `initialization_seconds`, while first-use compilation is visible in
`cold_step_seconds`. It then creates a fresh solver after process-global kernels
are warm, so the measured physical run starts at scenario time zero. Timed
steady-state regions exclude schema validation, serialization, viewer
rendering, and wake-probe sampling. Python also excludes Jaxtyping runtime
checks. Julia reports package startup/compilation, initialization, its first
cold step, and warmed steady-state work separately.

Reported performance includes median and p95 step latency, simulated seconds
per wall second, update throughput, peak resident memory, and internal
substeps. Reported diagnostics include energy, enstrophy, divergence, mass or
density drift where meaningful, solid leakage, wake width, and recirculation
area. FoilBench does not assign an aesthetic score.

Airfoil runs also sample transverse velocity 1.5 chords downstream during the
second half of the matched physical interval. The runner reports RMS
fluctuation, dominant frequency, Strouhal number, dominant spectral-power
fraction, and discrete frequency resolution. These values characterize
coherent and broadband wakes without treating either as an automated quality
rank.

For a scripted angle excursion that returns to its initial angle, the runner
also records recovery against the last pre-excursion baseline. Recovery means
both wake width and recirculation area have returned within 25% of that
baseline, with a two-cell floor so grid quantization cannot make the criterion
impossible. If this does not occur before the run ends, `recovery_elapsed` is
explicitly right-censored and `recovery_observed` is zero. The normalized
transverse RMS is additionally reported as `wake_mixing_index`.

The validation repertoire consists of uniform flow, Taylor-Green decay,
Poiseuille flow, a symmetric NACA 0012 case, and the pedagogical dynamic NACA
2412 scenario.
