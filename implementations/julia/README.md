# FoilBench Julia

This is the independent Phase 2A Julia implementation. It reads shared root
scenarios and conformance fixtures but does not import or host Python solvers.

From the repository root:

```powershell
julia --project=implementations/julia -e "using Pkg; Pkg.instantiate()"
julia --project=implementations/julia -e "using Pkg; Pkg.test()"
julia --project=implementations/julia implementations/julia/benchmark/benchmarks.jl
julia --project=implementations/julia implementations/julia/bin/foilbench-jl describe
julia --project=implementations/julia implementations/julia/bin/foilbench-jl bench benchmark-matrices/smoke.json
julia --project=implementations/julia implementations/julia/bin/foilbench-jl compare results/julia
```

Compilation and first-call costs will be reported separately from steady-state
solver timings.

The native GLMakie viewer supports Stable Fluids, D2Q9 TRT LBM, and blended
PIC/FLIP:

```powershell
julia --threads=auto --project=implementations/julia/viewer implementations/julia/bin/foilbench-jl view scenarios/airfoil/default.json
```

Use the mouse to drag the foil. Controls include Space pause, `R` reset,
`1`/`2`/`3` solver selection, `[`/`]` solver tuning, `-`/`+`/`0` Reynolds
control, `V` vorticity, `T` tracer mode, and `C` diagnostic cropping. Tuning
selects MacCormack/skew-RK2 transport for Stable Fluids and adjusts the
PIC/FLIP blend; LBM has no corresponding live parameter.
Run the viewer with at least two Julia threads so the GLMakie render loop
remains independent from solver work. GLMakie has its own committed `viewer/`
environment so solver-only tests and benchmarks do not pay its dependency or
precompile cost.

Julia 1.13 is the primary target and Julia 1.12 stays supported. The solver
environment has a single `Manifest.toml` resolved under 1.13 that also installs
on 1.12.6; the `julia-floor` CI job checks that. The `viewer/` environment keeps
two manifests because Julia 1.13 made `Zstd_jll` a standard library, so a
manifest resolved under 1.13 cannot install on 1.12:

- `viewer/Manifest.toml` is resolved under Julia 1.13. It is also what any Julia
  without its own versioned file uses, so a newer release never rewrites the 1.12
  manifest.
- `viewer/Manifest-v1.12.toml` is resolved under Julia 1.12 and is selected
  automatically by every 1.12.x.

After changing the viewer's dependencies, regenerate both, each under its own
Julia, and keep the third-party package versions identical (juliaup channels
shown):

```powershell
julia +1.13 --project=implementations/julia/viewer -e "using Pkg; Pkg.resolve()"
julia +1.12 --project=implementations/julia/viewer -e "using Pkg; Pkg.resolve()"
```

Run the preview performance acceptance gate with:

```powershell
julia --threads=auto --project=implementations/julia implementations/julia/benchmark/preview_gate.jl
```

The optional deterministic 2D chaotic-wake tools are:

```powershell
julia --project=implementations/julia implementations/julia/experiments/chaotic_wake_sweep.jl
julia --project=implementations/julia implementations/julia/experiments/paired_trajectory.jl
```
