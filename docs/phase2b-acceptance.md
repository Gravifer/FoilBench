# Phase 2B acceptance

Phase 2B completed and revalidated its automated engineering acceptance on
2026-08-03 after an extensive parallel QA and remediation pass. A further
TypeScript contract-reconciliation pass completed on 2026-08-09. It adds an
independent strict TypeScript implementation; it does not load Python or Julia
solvers. Final phase closure remains subject to the user-attention policy gate
recorded below.

## Delivered repertoire

- Stable Fluids on a MAC grid with RK2 backtracing, limited MacCormack,
  first-order semi-Lagrangian and skew-symmetric RK2 modes, implicit
  viscosity, projection, and moving-wall velocity.
- D2Q9 TRT LBM with lattice scaling, moving-wall interpolated bounce-back,
  deterministic uncovered cells, inlet/transverse/outlet treatment, and an
  outlet sponge.
- Four-particles-per-cell blended PIC/FLIP with quadratic transfers,
  deterministic PCG32 seeding, RK2 particle motion, population maintenance,
  and SDF collision handling.
- A Web Worker simulation owner and Three.js viewer with 8,192 passive
  tracers, batched path history, subtle vorticity, cropping, Reynolds control,
  live tuning, overlays, warm switching, and bounded recovery.
- Chromium-native benchmark execution with startup and steady-state timing,
  JSON/CSV output, optional canonical snapshots, wake-spectrum and recovery
  diagnostics, self-contained matched-run identity, and offline artifact
  comparison.

The accepted wake bar remains a coherent alternating vortex street. The
skew-RK2 control supports exploratory irregular wakes, but the dedicated
chaotic-wake sweep and paired-trajectory experiment remain optional
post-acceptance work.

## Reproduction

From the repository root:

```powershell
just ts-setup
just verify-typescript
just ts-view scenarios/airfoil/default.json stable-fluids
just ts-bench benchmark-matrices/smoke.json
just ts-preview-gate
```

The native commands are:

```powershell
npm --prefix implementations/typescript run check
npm --prefix implementations/typescript test
npm --prefix implementations/typescript run build
npm --prefix implementations/typescript run test:browser
npm --prefix implementations/typescript run gate:preview
```

## Automated evidence

- Strict TypeScript and ESLint pass.
- All 100 current Vitest checks pass shared RNG, geometry, scenario,
  canonical-layout, solver protocol, fidelity, recovery, and integration
  checks.
- All six directed solver conversions pass at both `4°` and `25°`, directly
  and through the viewer model.
- Uniform flow, Taylor–Green, quantitative Poiseuille profile/wall leakage,
  quantitative NACA 0012 symmetry/penetration, and actual dynamic NACA 2412
  diagnostic cases pass the matched tolerances.
- Unsupported thin-3D scenarios fail through the solver capability boundary.
- The production Vite build and live Chromium interaction smoke test pass.
- A snapshot-enabled browser matrix emits schema-valid manifests and
  little-endian C-order `.npy` fields. The Python reference loads all three
  TypeScript outputs with the declared canonical shapes.
- The `160 x 96` preview gate passes three repetitions for every solver. On
  the development machine, observed median ranges were:

  | Solver | Median step latency | Approximate steps/s |
  | --- | ---: | ---: |
  | Stable Fluids | `12.8–18.5 ms` | `54–78` |
  | D2Q9 TRT LBM | `94.8–97.2 ms` | `10.3–10.5` |
  | Blended PIC/FLIP | `77.1–96.8 ms` | `10.3–13.0` |

Generated benchmark results remain gitignored; rerun the gate for current
machine evidence.

## Post-acceptance QA remediation

The initial automated acceptance record preceded a deliberately blind,
high-effort review. That review found substantive defects despite the green
suite: incomplete canonical validation, nominal rather than physical LBM time
scaling, a nonconventional PIC/FLIP update, unbounded worker publication,
solver-only interactive timing, superficial airfoil/channel assertions, and
missing matched wake/recovery evidence. The focused correction commits are
`da7feb8`, `3ba149d`, `09288e8`, `7773965`, `0314c7e`, `a408952`, and
`d304a52`. The evidence above was regenerated after those corrections; it is
not inherited from the superseded implementation.

The 2026-08-09 reconciliation then addressed lessons captured by the proposed
revision 2 contracts:

- visible tracers use frozen-field midpoint RK2, an explicit display/material
  lifecycle, authoritative current-pose placement, and generation-safe paths;
- solver recovery uses an explicit restart operation rather than a fabricated
  time-zero canonical round trip;
- state revisions and structured failure evidence make import, stepping, and
  diagnostics transactions observable;
- all three solvers enforce bounded work and exact requested-time behavior,
  while Stable Fluids reports iterative convergence, LBM enforces its Mach,
  density, population, and TRT envelopes, and PIC/FLIP reports transfer and
  population health; and
- moving-wall work participates in substep selection, including relative-wall
  PIC/FLIP collision response and LBM wall/sweep scaling.

These changes reconcile TypeScript with revision 2; the revision remains
proposed repository-wide until its fixtures and the Python and Julia
reconciliations are complete.

## Open user-policy acceptance

The following decisions were deliberately deferred and must not be silently
settled by an agent:

- final fresh fallback policy after a rejected warm import;
- final drag cap, smoothing window, and pose-only hysteresis;
- whether to expose an every-step diagnostic mode;
- language-neutral viewer command transcripts; and
- the manual cross-platform GPU interaction matrix.

Their full questions and current normative minimums remain in the
[interactive viewer contract](../spec/interactive-viewer-contract.md#open-decisions).
The code is ready for that evaluation, but the roadmap intentionally keeps
the final policy gate open.
