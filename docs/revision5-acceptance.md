# Revision 5 acceptance

Revision 5 became FoilBench's accepted Phase 3 baseline on 2026-08-14. The
implementation under test was commit
`1459b068943d24a8645b90acfafc2216dc026108`; the specification activation and
this report were committed afterward and do not change solver behavior or
acceptance thresholds.

## Authoritative run

The authoritative invocation was:

```powershell
just verify-representative
```

The root invocation completed every static/native suite, representative gate,
four-producer benchmark and canonical-interchange cell, native comparer, and
paired-sensitivity preflight. It also completed the Python, Julia, and
TypeScript long chaos cells before the outer process reached its one-hour
execution budget. Only the missing Rust sweep and paired trajectory were then
run at the identical commit and configuration. The exact-roster validators
accepted the combined evidence; no completed cell was regenerated.

Generated, gitignored evidence is retained locally under
`results/revision5-acceptance/20260814-044922/`. Its
`representative-evidence.json` records the commit, contract identity, producer
rosters, artifact locations, and continuation note.

Runtime context:

- Razer Blade 17 (2022), Intel Core i7-12800H, 20 logical CPUs, 34.0 GB
  physical memory;
- Windows 11 Pro for Workstations, build 26200, 64-bit;
- Python 3.14.6 with NumPy 2.4.6, Julia 1.12.6, Node.js 24.18.0 with npm
  12.0.2, and Rust/Cargo 1.96.0; and
- AC power connected for the accepted performance and long-trajectory work.

## Representative and interchange results

All native suites, strict static checks, the Vite production build, Chromium
smokes, native Rust tests, Clippy, and the real `wasm32-unknown-unknown` build
passed. Startup, scheduled control, representative preview, warm switching,
fallback transactions, and production-browser cells passed for every target
required by the Revision 5 fixture.

Every native solver exceeded the unchanged 10 warmed steps/s development-
machine floor at 160 x 96. The final Python rates were 39.89 steps/s for Stable
Fluids, 41.79 for D2Q9 LBM, and 19.46 for PIC/FLIP. Julia,
TypeScript/browser-worker, Rust/native, and Rust/WASM also passed the same
threshold; hosted CI records rates but does not enforce this machine-specific
absolute floor.

The independently emitted 128 x 64 smoke artifacts retained these reciprocal
median step times (steps/s):

| Producer | Stable Fluids | D2Q9 LBM | PIC/FLIP |
| --- | ---: | ---: | ---: |
| Python/native | 100.25 | 79.46 | 43.62 |
| Julia/native | 64.23 | 57.00 | 42.70 |
| TypeScript/browser-worker | 61.35 | 44.84 | 40.82 |
| Rust/native | 62.58 | 70.70 | 32.50 |

Python/native, Julia/native, TypeScript/browser-worker, and Rust/native each
emitted the complete smoke matrix. Python, Julia, TypeScript, and Rust native
comparers all accepted the combined exact roster. Every canonical reader and
destination family accepted the required version-1/version-2 interchange
cases, including producer/execution-target and geometry identity.

## Paired sensitivity and chaotic-wake evidence

The 0.1-second full-resolution preflight first round-tripped both members of
each pair through symmetric canonical reconstruction. Both imports had to be
accepted before any separation was measured.

| Producer | Requested epsilon | Realized initial wake RMS | Realized/epsilon |
| --- | ---: | ---: | ---: |
| Python/native | 1.0e-4 | 8.325e-6 | 0.0833 |
| Julia/native | 1.0e-4 | 8.325e-6 | 0.0833 |
| TypeScript/node | 1.0e-4 | 8.326e-6 | 0.0833 |
| Rust/native | 1.0e-4 | 8.325e-6 | 0.0833 |

The complete 12-second paired trajectories then produced:

| Producer | Amplification | Finite-time exponent | Fit R² |
| --- | ---: | ---: | ---: |
| Python/native | 23,716.8 | 1.6895 | 0.9741 |
| Julia/native | 25,728.5 | 1.6870 | 0.9739 |
| TypeScript/node | 37,372.6 | 1.6579 | 0.9702 |
| Rust/native | 69,373.4 | 1.8809 | 0.9982 |

All sixteen sweep cells—four declared cases from each native producer—passed
the irregular multiscale-wake, spectral-broadening, enstrophy-variation, and
bounded-divergence thresholds. These are qualitative two-dimensional wake
claims. They do not claim three-dimensional turbulence, quantitative stall
prediction, or automated visual quality.

## Result

Revision 5 satisfies the required native numerical, fidelity, interchange,
artifact, comparison, and chaotic-extension roster, together with the
Rust/WASM protocol, deterministic-state, recovery, switching, preview, and
production-browser roster. Phase 3's three 2D Rust solvers are therefore the
accepted production core for both native and WASM targets. D3Q19 and shallow
periodic 3D remain deferred.

A later non-semantic errata pass closed the remaining PR #2 quality findings
and added truthful full-control-history mixing/recovery evidence without
changing this acceptance decision. See the
[Revision 5 quality addendum](revision5-quality-addendum.md).
