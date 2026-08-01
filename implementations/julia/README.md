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
`1`/`2`/`3` solver selection, `[`/`]` PIC/FLIP blending, `-`/`+`/`0`
Reynolds control, `V` vorticity, `T` tracer mode, and `C` diagnostic cropping.
Run the viewer with at least two Julia threads so the GLMakie render loop
remains independent from solver work. GLMakie has its own committed `viewer/`
environment so solver-only tests and benchmarks do not pay its dependency or
precompile cost.

Run the preview performance acceptance gate with:

```powershell
julia --threads=auto --project=implementations/julia implementations/julia/benchmark/preview_gate.jl
```

The optional deterministic 2D chaotic-wake tools are:

```powershell
julia --project=implementations/julia implementations/julia/experiments/chaotic_wake_sweep.jl
julia --project=implementations/julia implementations/julia/experiments/paired_trajectory.jl
```
