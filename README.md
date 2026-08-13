# FoilBench

FoilBench is a solver-swappable, inspectable airfoil-flow benchmark. Phase 1
is complete and contains the robustly typed Python reference implementation,
including an accepted opt-in deterministic 2D chaotic-wake extension. Phase
2A is complete with an independent Julia implementation. Phase 2B and the
three-language contract closure are complete and revalidated after extensive
QA. Revision 4 remains the completed three-language baseline after full-size,
interchange, fallback, and chaotic-extension acceptance. Phase 3 is complete
and provides
the complete Rust 2D solver repertoire: native and WASM Stable Fluids, D2Q9
TRT LBM, and blended PIC/FLIP built from one shared core. Revision 5 is the
accepted native and Rust/WASM baseline.

The native implementations share scenarios, schemas, and result artifacts
without importing one another's solver code. The deliberate production
exception is the Three.js simulation worker, which can host the shared Rust
core through its WASM boundary as an alternative to the independent
TypeScript solvers.

## Command shortcuts

With [`just`](https://just.systems/) installed, run `just` at the repository
root to list the available shortcuts. Common commands are:

```powershell
just setup
just py-view
just jl-view
just ts-view
just rs-describe
just ts-preview-gate
just py-chaos
just verify
```

Viewer and benchmark recipes accept optional paths and solver IDs; for
example, `just jl-view scenarios/airfoil/fixed-stall.json pic-flip` and
`just py-bench benchmark-matrices/smoke.json`. The full commands remain
documented below and continue to work directly.

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

## TypeScript quick start

Phase 2B independently implements all three solvers, a Web Worker/Three.js
viewer, Chromium benchmarks, canonical snapshots, warm switching, and
graceful recovery. From the repository root:

```powershell
npm --prefix implementations/typescript ci
npm --prefix implementations/typescript run setup:browser
npm --prefix implementations/typescript run check
npm --prefix implementations/typescript test
npm --prefix implementations/typescript run describe
npm --prefix implementations/typescript run view -- scenarios/airfoil/default.json stable-fluids
npm --prefix implementations/typescript run view -- scenarios/airfoil/default.json stable-fluids rust-wasm
npm --prefix implementations/typescript run bench -- benchmark-matrices/smoke.json
npm --prefix implementations/typescript run gate:preview
```

The viewer prints its local URL; open it in Chromium. Controls are `1/2/3`
for solvers, left-drag for foil pose, `Space` pause, `R` reset, `+/-/0`
Reynolds control, `[/]` solver tuning, and `V/T/C` for vorticity, tracer mode,
and diagnostic cropping.

## Rust/WASM

The platform-neutral core implements PCG32, validated typed scenarios, NACA
geometry, MAC-grid numerics, canonical version 1/2 state handling, the solver
lifecycle, and Stable Fluids in both `f32` and `f64`. The native crate owns
NPY/JSON/CSV artifacts and benchmark commands. The WASM crate exposes the same
three Rust solvers through the existing TypeScript worker:

```powershell
cargo test --manifest-path implementations/rust/Cargo.toml --workspace --locked
cargo run --quiet --manifest-path implementations/rust/Cargo.toml --locked -p foilbench-native -- describe
just ts-view scenarios/airfoil/default.json stable-fluids rust-wasm
```

The Rust/WASM selector offers Stable Fluids, D2Q9 TRT LBM, and blended
PIC/FLIP through keys `1`, `2`, and `3`.

The accepted Revision 5 contract is the current authority. Revision 4 remains
documented as the completed three-language baseline from which Phase 3 began.

Run every implemented language's native checks through the root convenience
entry point (or pass `-Python`, `-Julia`, `-TypeScript`, or `-Rust` to select
one):

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
[Phase 2B acceptance](docs/phase2b-acceptance.md),
[Revision 4 acceptance](docs/revision4-acceptance.md),
[Revision 5 acceptance](docs/revision5-acceptance.md),
[benchmarking guide](docs/benchmark-methodology.md), and the
[implementation roadmap](docs/implementation-roadmap.md).

The language-neutral specifications are indexed by the
[FoilBench contract suite](spec/README.md). Its manifest distinguishes the
solver protocol, solver repertoire, accepted-step validity, interactive
viewer behavior, artifacts, and benchmark methodology so later languages do
not infer policy from one reference implementation.

The accepted opt-in investigation of deterministic 2D chaotic wakes is
documented in [the Phase 1 chaotic-wake extension](docs/chaotic-wake-experiment.md).
