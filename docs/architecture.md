# Architecture

FoilBench is a polyglot monorepo. `spec/`, `scenarios/`, and
`benchmark-matrices/` are language-neutral. Every directory under
`implementations/` is an independent implementation with its own solvers,
viewer, tests, dependency lock, and native benchmark runner.

Python is the semantic reference, not the presumed performance winner.
Cross-language comparison happens through schema-validated result artifacts.
No implementation is required to load another language's solver.

## Interactive and reference domains

The interactive airfoil scenario uses a compact `5c × 3c` domain at
`160 × 96`: 32 cells per chord and approximately four cells through the
maximum thickness of a NACA 0012/2412 section. This spends the preview budget
on the foil and near wake rather than a long downstream canvas.

The separate `scenarios/airfoil/reference.json` retains the wide `8c × 4c`
domain and strict pressure tolerance for validation and benchmark matrices.
Changing preview compromises therefore does not silently change reference
runs.

## Python data flow

1. A scenario supplies the domain, airfoil, boundary conditions, and control
   schedule.
2. A solver advances its private state and exposes sampled velocity plus a
   canonical cell-centered state.
3. The viewer owns passive display tracers and their path history.
4. Warm switching exports canonical state and imports it into another Python
   solver at a completed step boundary.
5. The benchmark runner times only solver work and emits portable artifacts.

The viewer advances by the scenario's fixed `output_dt`; rendering cadence
never changes a solver's physical or lattice-unit scaling. A single-owner
simulation worker applies queued controls only between completed steps and
publishes immutable, latest-only render snapshots. The Pyglet/OpenGL thread
never reads live solver, tracer, or history arrays. It can therefore continue
rendering and accepting input while a slower solver step is in progress.

The worker does not catch up or conceal throughput: it attempts at most 60
solver steps per wall second and advances one fixed `output_dt` per completed
step. When a backend is slower than real time, the overlay continues to report
its actual solver steps/s and simulated-seconds/wall-second values.

At coarse preview resolutions, D2Q9 may not represent the requested Reynolds
number while keeping TRT safely separated from `tau=0.5`. It therefore clamps
to `tau>=0.52`, reports `effective_reynolds` and an explicit warning, and
displays `Re_eff` in the overlay. Once resolution makes the requested lattice
viscosity stable, no clamp is applied. Its open domain uses Zou-He velocity
reconstruction, a convective outlet, and weak far-field/outlet sponge layers
to suppress corner and reflection modes.

The canonical state uses semantic axes `z y x component`. Two-dimensional
states store a singleton `z` axis. Private pressure history, lattice
populations, and solver particles are deliberately excluded.

## Viewer GPU smoke test

The automated viewer tests exercise state, controls, immutable worker
snapshots, clean thread shutdown, tracer paths, and warm switching without
creating a window. On a machine with an OpenGL 3.3 context, run:

```powershell
uv run --project implementations/python foilbench-py view scenarios/airfoil/default.json
```

Confirm that the foil, density-scaled tracer points, batched path afterimages, and
diagnostic overlay render. Drag the foil, pause and reset, select each solver
with `1`/`2`/`3`, and adjust PIC/FLIP blending with `[`/`]`. Switching must
retain visible tracer paths and show the conversion transient without a
crossfade.

Use `-` and `+` to change the requested Reynolds number in quarter-decade
steps; `0` restores the scenario value. The selection changes solver viscosity
online and survives warm switching. To make the visual response legible, wall-
clock playback scales mildly: a tenfold Reynolds increase advances simulated
time 1.5 times as fast, clamped to 0.5–2 times. Tracers are not independently
sped up—they remain passive and advance through the same physical interval as
the solver. LBM continues to display `Re_eff` when its relaxation clamp cannot
honor the requested value.

Runtime Reynolds control has a recovery circuit breaker. Two consecutive
solver failures, or three failures within five wall-clock seconds, restore the
scenario Reynolds number before a fresh restart at the visible foil angle.
The overlay records the automatic Reynolds reset. Repeated failure at the
scenario value pauses the worker instead of entering an endless recovery loop.

Press `V` to toggle the dynamically normalized signed-vorticity layer. Press
`T` to switch between display tracers and material tracers. Display tracers
have deterministic finite lifetimes and respawn throughout the domain so
closed recirculation regions do not become visually empty. Material tracers
only re-enter at the inlet and intentionally preserve depletion and residence
effects. Neither mode changes solver state.

The viewer targets approximately 256 display tracers per square chord and
clamps the total to 2,048–8,192. The compact preview therefore uses about
3,840 tracers while the wide reference canvas uses 8,192, keeping the visual
density comparable.

## Thin periodic depth

Schemas already describe dimensionality, spanwise extent, and periodic axes.
Phase 1 solvers advertise 2D support only. A future shallow 3D run represents a
periodically repeated airfoil section, not a finite wing; it therefore has no
tips or tip vortices. The default student view will remain a mid-span 2D slice.
