# FoilBench

FoilBench is a solver-swappable, inspectable airfoil-flow benchmark. Phase 1
is complete and contains the robustly typed Python reference implementation,
including an accepted opt-in deterministic 2D chaotic-wake extension. Phase
2A is complete with an independent Julia implementation; TypeScript follows
in Phase 2B, and Rust/WASM in Phase 3.

The implementations share scenarios, schemas, and result artifacts. They do
not import or host one another's solvers.

## Python quick start

```powershell
uv sync --project implementations/python --all-groups
uv run --project implementations/python foilbench-py describe
uv run --project implementations/python foilbench-py view scenarios/airfoil/default.json
uv run --project implementations/python foilbench-py bench benchmark-matrices/smoke.json
```

Run checks:

```powershell
uv run --project implementations/python ruff check implementations/python
uv run --project implementations/python pyright implementations/python
uv run --project implementations/python pytest -c implementations/python/pyproject.toml
```

## Julia quick start

The Phase 2A package independently implements the three solvers, result and
canonical-state artifacts, benchmarks, graceful warm switching, and a native
GLMakie viewer. From the repository root:

```powershell
julia --project=implementations/julia -e "using Pkg; Pkg.instantiate()"
julia --project=implementations/julia -e "using Pkg; Pkg.test()"
julia --project=implementations/julia implementations/julia/bin/foilbench-jl describe
julia --threads=auto --project=implementations/julia/viewer implementations/julia/bin/foilbench-jl view scenarios/airfoil/default.json
julia --project=implementations/julia implementations/julia/bin/foilbench-jl bench benchmark-matrices/smoke.json
```

Run every implemented language's native checks through the root convenience
entry point (or pass `-Python` / `-Julia` to select one):

```powershell
pwsh -NoProfile -File tools/verify.ps1
```

## Legacy visualization

The original Manim experiment is intentionally absent from the new tree. It is
preserved in Git commit `f71d1fba327cf67a1513764bb8596ac4faf99cb2`:

```powershell
git show f71d1fb:particle_airfoil_stall.py
```

See [architecture](docs/architecture.md), [Phase 1 acceptance](docs/phase1-acceptance.md),
[Phase 2A acceptance](docs/phase2a-acceptance.md),
[benchmark methodology](docs/benchmark-methodology.md), and the
[implementation roadmap](docs/implementation-roadmap.md).

The accepted opt-in investigation of deterministic 2D chaotic wakes is
documented in [the Phase 1 chaotic-wake extension](docs/chaotic-wake-experiment.md).
