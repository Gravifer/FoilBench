# Benchmarking FoilBench

This is a descriptive guide for people running and reading FoilBench
benchmarks. It is not a source of conformance requirements. Exact run
boundaries, artifact identity, metric definitions, and acceptance behavior are
owned by the
[benchmark methodology contract](../spec/contracts/benchmark-methodology.md).

## Running benchmarks

The root `justfile` provides equivalent shortcuts for each implementation:

```powershell
just py-bench
just jl-bench
just ts-bench
```

Each recipe uses the smoke matrix by default and writes generated, gitignored
artifacts under `results/`. Pass a different matrix to compare resolutions,
durations, or solver subsets. The corresponding `py-compare`, `jl-compare`,
and `ts-compare` recipes read result directories without hosting another
language's solver.

Use `just verify` for ordinary development checks. The much longer
`just verify-representative` command is the contract-acceptance tier: it adds
full-size solver paths, exact three-language participation, canonical
interchange, warm-switch and fallback transactions, and the optional
chaotic-wake evidence. It should not be used as a routine inner-loop test.

## Reading an artifact

Initialization and the first cold step are reported separately from warmed
solver work. Steady-state results include median and p95 step latency,
simulated seconds per wall second, update throughput, internal substeps,
memory meaning, the final step report, and solver diagnostics. A result also
repeats its physical inputs so comparers can reject mismatched runs rather
than trusting filenames.

Wake, leakage, energy, enstrophy, divergence, and recovery fields are
diagnostics, not an automated aesthetic score. Performance comparisons are
meaningful only when the physical identity and declared solver configuration
match; different languages need not produce bit-identical trajectories.

Exploratory comparison may use partial or historical result directories.
Acceptance comparison additionally requires the complete declared matrix and
the exact producer roster. The normative contract defines the distinction and
the schema-backed failure rules.

The accepted Revision 4 machine context, measured rates, paired-sensitivity
initialization, and pass summary are recorded in
[Revision 4 acceptance](revision4-acceptance.md). The older Phase 1 interactive
snapshot remains available in
[the 2026-07-31 benchmark record](benchmark-results-2026-07-31.md).
