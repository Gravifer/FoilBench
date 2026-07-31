# FoilBench

FoilBench is a solver-swappable, inspectable airfoil-flow benchmark. Phase 1
is complete and contains the robustly typed Python reference implementation.
Julia and
TypeScript peers are planned for Phase 2; Rust and WASM are planned for Phase 3.

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
uv run --project implementations/python pyright
uv run --project implementations/python pytest
```

## Legacy visualization

The original Manim experiment is intentionally absent from the new tree. It is
preserved in Git commit `f71d1fba327cf67a1513764bb8596ac4faf99cb2`:

```powershell
git show f71d1fb:particle_airfoil_stall.py
```

See [architecture](docs/architecture.md), [benchmark methodology](docs/benchmark-methodology.md),
and the [implementation roadmap](docs/implementation-roadmap.md).
