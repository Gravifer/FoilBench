# Phase 2B acceptance

Phase 2B has completed its automated engineering acceptance. It adds an
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
  JSON/CSV output, optional canonical snapshots, and offline artifact
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
- The current Vitest repertoire passes shared RNG, geometry, scenario,
  canonical-layout, solver protocol, fidelity, recovery, and integration
  checks.
- All twelve directed solver conversions pass at both `4°` and `25°`.
- Uniform flow, Taylor–Green, Poiseuille, NACA 0012, and dynamic diagnostic
  cases pass the matched tolerances.
- Unsupported thin-3D scenarios fail through the solver capability boundary.
- The production Vite build and live Chromium interaction smoke test pass.
- A snapshot-enabled browser matrix emits schema-valid manifests and
  little-endian C-order `.npy` fields. The Python reference loads all three
  TypeScript outputs with the declared canonical shapes.
- The `160 x 96` preview gate passes three repetitions for every solver. On
  the development machine, observed median ranges were:

  | Solver | Median step latency | Approximate steps/s |
  | --- | ---: | ---: |
  | Stable Fluids | `18.2–19.7 ms` | `51–55` |
  | D2Q9 TRT LBM | `58.7–71.8 ms` | `14–17` |
  | Blended PIC/FLIP | `60.4–78.3 ms` | `13–17` |

Generated benchmark results remain gitignored; rerun the gate for current
machine evidence.

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
