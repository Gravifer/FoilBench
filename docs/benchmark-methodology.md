# Benchmark methodology

The latest local Phase 1 interactive snapshot is recorded in
[benchmark-results-2026-07-31.md](benchmark-results-2026-07-31.md).

Runs are matched by physical domain, resolution, Reynolds number, control
history, precision, seed, and simulated duration. Each solver may choose the
stable internal timestep and number of substeps it needs.

The runner reports initialization and compilation separately from steady-state
work. Timed regions exclude schema validation, serialization, viewer rendering,
and Jaxtyping runtime checks.

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

The validation repertoire consists of uniform flow, Taylor-Green decay,
Poiseuille flow, a symmetric NACA 0012 case, and the pedagogical dynamic NACA
2412 scenario.
