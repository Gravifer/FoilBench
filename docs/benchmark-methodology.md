# Benchmark methodology

Status: accepted normative component of `foilbench-phase2-v1`, revision 3.

The latest historical Phase 1 interactive snapshot is recorded in
[benchmark-results-2026-07-31.md](benchmark-results-2026-07-31.md). Python,
Julia, and TypeScript now emit the same result-schema identity and canonical
snapshot semantics for offline cross-language comparison.

Runs are matched per solver family by physical domain, resolution, requested
and effective Reynolds numbers, normalized numerical solver configuration,
control history, precision, seed, and simulated duration. Each solver may
choose the stable internal timestep and number of substeps it needs.

Every result JSON identifies the accepted contract with `contract_id` and
`contract_revision`, then repeats the matched physical identity directly:
benchmark-matrix and scenario IDs, repetition, bounds and periodic axes,
resolution, requested and effective Reynolds numbers, normalized solver
configuration, freestream, foil specification, control history, requested and
actually simulated duration, output interval, precision, and seed. Comparisons therefore
do not have to infer physical equivalence from a filename or scenario ID.
Comparers schema-validate their inputs, apply cross-field semantic validation,
and compare decoded numeric structures rather than JSON spelling or object-key
order. They reject mismatched physical identity rather than producing a
misleading performance table.

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
per wall second, update throughput, and internal substeps. Memory is paired
with an explicit measurement meaning: native runners report process RSS,
browser runners may report a browser estimate, and unavailable measurements
remain null rather than masquerading as zero. Runtime and worker startup are
separate nullable fields.

The artifact also records final and diagnostic solver-state revisions plus the
last completed step report. That report preserves the requested and advanced
intervals, substeps, maximum speed, revision, warnings, and solver-family
validity evidence. A successful artifact cannot silently combine stale
diagnostics with a newer final field. Classified failures use a structured
kind, reason, stage, message, and evidence object; unexpected failures remain
distinguishable from numerical rejection. Initialization and cold-step failures
produce ordinary failed artifacts and do not abort the remainder of a matrix.

Reported diagnostics include energy, enstrophy, divergence, mass or
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
