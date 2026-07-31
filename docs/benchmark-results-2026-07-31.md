# Python Phase 1 bake-off — 2026-07-31

This local snapshot measures commit
`60a29031b01bc19ac2d938d4d553888092780647` on Windows 11 with Python
3.14.6, NumPy 2.4.6, and 20 logical CPUs. Generated JSON and CSV artifacts
remain under the gitignored `results/` tree.

The throughput matrix runs three timed repetitions after solver warm-up. Values
below are medians across those repetitions. Solver timing excludes rendering,
serialization, schema validation, runtime type checking, and wake-probe
sampling.

| Solver | Grid | Cells/chord | Median step | Median p95 | Steps/s |
|---|---:|---:|---:|---:|---:|
| Stable Fluids | 160×96 | 32 | 41.67 ms | 48.17 ms | 24.00 |
| PIC/FLIP | 160×96 | 32 | 44.99 ms | 71.00 ms | 22.23 |
| D2Q9 LBM | 160×96 | 32 | 57.75 ms | 70.01 ms | 17.31 |
| Stable Fluids | 240×144 | 48 | 102.37 ms | 131.08 ms | 9.77 |
| D2Q9 LBM | 240×144 | 48 | 151.70 ms | 179.97 ms | 6.59 |
| PIC/FLIP | 240×144 | 48 | 212.82 ms | 258.51 ms | 4.70 |

Relative to the earlier Phase 1 snapshot, the 160×96 LBM median fell from
258.00 ms to 57.75 ms and PIC/FLIP fell from 118.98 ms to 44.99 ms. LBM now
uses a fused compiled TRT collision while retaining vectorized interpolated
wall streaming. PIC/FLIP avoids redundant gathers, caches static grid geometry,
and uses a bounded 0.75 CFL; violent wall motion still selects additional
substeps from wall speed.

The fixed-stall matrix runs each solver for three simulated seconds at 25
degrees. These are short-horizon comparable diagnostics, not a truth score or
an automated visual-quality score.

| Solver | Grid | Median step | Sim/wall | Enstrophy | Wake width | Recirculation |
|---|---:|---:|---:|---:|---:|---:|
| Stable Fluids | 160×96 | 38.86 ms | 0.418 | 1.344 | 1.938 | 0.268 |
| D2Q9 LBM | 160×96 | 42.93 ms | 0.372 | 1.848 | 0.812 | 0.229 |
| PIC/FLIP | 160×96 | 85.70 ms | 0.183 | 1.130 | 1.844 | 0.290 |
| D2Q9 LBM | 240×144 | 134.14 ms | 0.124 | 2.694 | 0.771 | 0.211 |
| Stable Fluids | 240×144 | 149.63 ms | 0.116 | 1.646 | 1.958 | 0.260 |
| PIC/FLIP | 240×144 | 210.25 ms | 0.076 | 1.629 | 1.938 | 0.264 |

All six fixed-stall runs completed with finite state and zero reported solid
leakage. The downstream transverse probe reported nonzero RMS fluctuation and
a dominant spectral component for every run. The retained second-half window
is 1.5 simulated seconds, so its frequency resolution is 0.667 in nondimensional
units; every run selected that first nonzero bin with 0.91–0.95 of windowed
spectral power. This establishes a coherent unsteady wake but is not a
precision estimate of shedding frequency or evidence of three-dimensional
turbulence.

PIC/FLIP ended with approximately 0.9% empty fluid cells between its periodic
population-maintenance passes. LBM reported its explicit coarse-grid
relaxation clamp: effective Reynolds numbers were approximately 366 and 576 at
32 and 48 cells/chord, respectively.

The 160×96 preview tier now sustains 17–24 calm-flow solver updates/s and
12–26 updates/s in developed fixed stall. Rendering remains independently
scheduled at 60 Hz through latest-only immutable snapshots. The 240×144 tier
remains a reference/inspection setting rather than a real-time interactive
setting.
