# FoilBench Julia

This is the independent Phase 2A Julia implementation. It reads shared root
scenarios and conformance fixtures but does not import or host Python solvers.

From the repository root:

```powershell
julia --project=implementations/julia -e "using Pkg; Pkg.instantiate()"
julia --project=implementations/julia -e "using Pkg; Pkg.test()"
julia --project=implementations/julia implementations/julia/benchmark/benchmarks.jl
```

Compilation and first-call costs will be reported separately from steady-state
solver timings. GLMakie is deliberately deferred until the headless solver
contracts and fidelity cases are established.
