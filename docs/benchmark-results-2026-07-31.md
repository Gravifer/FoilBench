# Python interactive bake-off — 2026-07-31

This local snapshot measures commit
`5c22b0fb15726d50c9ae2a743adc15a890bea06f` on Windows 11 with Python
3.14.6, NumPy 2.4.6, and 20 logical CPUs. Generated JSON and CSV artifacts
remain under the gitignored `results/` tree.

The throughput matrix runs three timed repetitions after solver warm-up. Values
below are medians across those repetitions. Solver timing excludes rendering,
serialization, schema validation, and runtime type checking.

| Solver | Grid | Cells/chord | Median step | Median p95 | Steps/s |
|---|---:|---:|---:|---:|---:|
| Stable Fluids | 160×96 | 32 | 71.91 ms | 83.41 ms | 13.91 |
| PIC/FLIP | 160×96 | 32 | 118.98 ms | 163.52 ms | 8.40 |
| D2Q9 LBM | 160×96 | 32 | 258.00 ms | 316.33 ms | 3.88 |
| Stable Fluids | 240×144 | 48 | 128.23 ms | 170.86 ms | 7.80 |
| PIC/FLIP | 240×144 | 48 | 279.79 ms | 336.15 ms | 3.57 |
| D2Q9 LBM | 240×144 | 48 | 734.24 ms | 933.93 ms | 1.36 |

The fixed-stall matrix runs each solver for three simulated seconds at 25
degrees. These are short-horizon comparable diagnostics, not a truth score or
an automated visual-quality score.

| Solver | Grid | Median step | Sim/wall | Enstrophy | Wake width | Recirculation |
|---|---:|---:|---:|---:|---:|---:|
| Stable Fluids | 160×96 | 47.77 ms | 0.337 | 1.344 | 1.938 | 0.268 |
| PIC/FLIP | 160×96 | 108.32 ms | 0.148 | 1.131 | 1.844 | 0.290 |
| D2Q9 LBM | 160×96 | 259.43 ms | 0.059 | 1.848 | 0.813 | 0.229 |
| Stable Fluids | 240×144 | 173.86 ms | 0.097 | 1.646 | 1.958 | 0.260 |
| PIC/FLIP | 240×144 | 511.17 ms | 0.032 | 1.435 | 1.917 | 0.281 |
| D2Q9 LBM | 240×144 | 766.11 ms | 0.021 | 2.695 | 0.771 | 0.211 |

All runs completed with finite state and zero reported solid leakage. PIC/FLIP
had 0.7–0.9% empty fluid cells at the final sampled frame, which fell between
its eight-step population-maintenance boundaries. LBM reported its explicit
coarse-grid relaxation clamp: effective Reynolds numbers were approximately
366 and 576 at 32 and 48 cells/chord, respectively.

None of these NumPy reference backends can produce one physical `1/60 s` step
per rendered 60 Hz frame on this machine. The viewer should therefore decouple
render/input work from simulation and publish latest-only completed snapshots.
That improves interaction latency without misrepresenting solver throughput.
