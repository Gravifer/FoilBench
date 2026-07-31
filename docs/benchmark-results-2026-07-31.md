# Python Phase 1 bake-off — 2026-07-31

This local snapshot measures commit
`62bd58314634151476fa8a936da0afdfbddb0a64` on Windows 11 with Python
3.14.6, NumPy 2.4.6, and 20 logical CPUs. Generated JSON and CSV artifacts
remain under the gitignored `results/` tree.

The throughput matrix runs three timed repetitions after solver warm-up. Values
below are medians across those repetitions. Solver timing excludes rendering,
serialization, schema validation, runtime type checking, and wake-probe
sampling.

| Solver | Grid | Cells/chord | Median step | Median p95 | Steps/s |
|---|---:|---:|---:|---:|---:|
| Stable Fluids | 160×96 | 32 | 19.78 ms | 20.72 ms | 50.56 |
| D2Q9 LBM | 160×96 | 32 | 37.26 ms | 40.97 ms | 26.84 |
| PIC/FLIP | 160×96 | 32 | 43.50 ms | 68.74 ms | 22.99 |
| Stable Fluids | 240×144 | 48 | 57.17 ms | 73.52 ms | 17.49 |
| D2Q9 LBM | 240×144 | 48 | 120.67 ms | 141.90 ms | 8.29 |
| PIC/FLIP | 240×144 | 48 | 202.00 ms | 248.23 ms | 4.95 |

Relative to the initial Phase 1 snapshot, the 160×96 LBM median fell from
258.00 ms to 37.26 ms and PIC/FLIP fell from 118.98 ms to 43.50 ms. LBM now
uses a fused compiled TRT collision while retaining vectorized interpolated
wall streaming. PIC/FLIP avoids redundant gathers, caches static grid geometry,
and uses a bounded 0.75 CFL; violent wall motion still selects additional
substeps from wall speed.

The fixed-stall matrix runs each solver for three simulated seconds at 25
degrees. These are short-horizon comparable diagnostics, not a truth score or
an automated visual-quality score.

| Solver | Grid | Median step | Sim/wall | Enstrophy | Wake width | Recirculation |
|---|---:|---:|---:|---:|---:|---:|
| Stable Fluids | 160×96 | 19.51 ms | 0.780 | 1.352 | 1.938 | 0.268 |
| D2Q9 LBM | 160×96 | 50.33 ms | 0.322 | 1.846 | 0.844 | 0.227 |
| PIC/FLIP | 160×96 | 85.85 ms | 0.189 | 1.045 | 1.781 | 0.274 |
| D2Q9 LBM | 240×144 | 124.48 ms | 0.130 | 2.677 | 0.792 | 0.213 |
| Stable Fluids | 240×144 | 140.93 ms | 0.119 | 1.656 | 1.958 | 0.259 |
| PIC/FLIP | 240×144 | 227.37 ms | 0.071 | 1.409 | 1.917 | 0.261 |

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

The 160×96 preview tier now sustains 23–51 calm-flow solver updates/s and
12–51 updates/s in developed fixed stall. Rendering remains independently
scheduled at 60 Hz through latest-only immutable snapshots. The 240×144 tier
remains a reference/inspection setting rather than a real-time interactive
setting.

A separate 64×32 diagnostic run exercised the complete
4°→14°→25°→4° schedule. All solvers completed and emitted mixing and recovery
fields. LBM met the baseline-relative recovery criterion after 2.03 simulated
seconds. Stable Fluids and PIC/FLIP had not met it after the four-second final
hold, so their reported durations are correctly right-censored rather than
declared recovered.
