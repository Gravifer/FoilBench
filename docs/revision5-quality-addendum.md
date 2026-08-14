# Revision 5 quality and errata closure

This addendum records a non-semantic quality pass over accepted Revision 5.
The numerical thresholds, artifact formats, solver repertoire, contract
identifier, accepted date, and PCG32 streams are unchanged.

The implementation commit under test was
`3d72a0e3b57cb3c5939436c055a783f7842b24b8`. Documentation was committed
afterward and does not alter the tested implementation.

## Verification

The complete non-representative verification command was:

```powershell
pwsh -NoProfile -File tools/verify.ps1
```

It passed specification/schema validation, Ruff, strict Pyright, 202 Python
tests, the complete Julia package tests and GLMakie load check, TypeScript
strict checks and 145 tests, the Vite production build, both production
Chromium smokes, Rust formatting and Clippy, 64 Rust native tests, and the
real `wasm32-unknown-unknown` target check. The previously accepted
representative, performance, sensitivity, and chaotic-wake tiers were not
rerun because their inputs and accepted behavior did not change.

Runtime context was Windows on the Revision 5 development machine with
Python 3.14.6, Julia 1.12.6, Node.js 24.18.0, npm 12.0.2, and Rust/Cargo
1.96.0.

## Scheduled mixing and recovery evidence

The new erratum evidence was generated exactly once with:

```powershell
pwsh -NoProfile -File tools/run_scheduled_fidelity.ps1
```

All twelve required cells completed the full 22-second 4°→14°→25°→4°
history at 32×20. The TypeScript implementation is correctly identified as
`typescript/browser-worker`; the other three producers are native. Generated
artifacts remain gitignored under
`results/revision5-quality/3d72a0e3b57cb3c5939436c055a783f7842b24b8/scheduled-fidelity/`.

| Producer | Solver | Mixing index | Recovery | Elapsed (s) |
| --- | --- | ---: | --- | ---: |
| Python/native | Stable Fluids | 0.0121255 | right-censored | 4.0000 |
| Python/native | D2Q9 LBM | 0.0135943 | observed | 0.0000 |
| Python/native | PIC/FLIP | 0.0407701 | observed | 1.4500 |
| Julia/native | Stable Fluids | 0.0123887 | right-censored | 4.0000 |
| Julia/native | D2Q9 LBM | 0.0135829 | observed | 0.0166 |
| Julia/native | PIC/FLIP | 0.0422943 | observed | 1.7665 |
| TypeScript/browser-worker | Stable Fluids | 0.0121276 | right-censored | 4.0000 |
| TypeScript/browser-worker | D2Q9 LBM | 0.0137269 | observed | 0.0000 |
| TypeScript/browser-worker | PIC/FLIP | 0.0145748 | observed | 1.6167 |
| Rust/native | Stable Fluids | 0.0116813 | right-censored | 4.0000 |
| Rust/native | D2Q9 LBM | 0.0137270 | observed | 0.0000 |
| Rust/native | PIC/FLIP | 0.0365940 | observed | 0.0000 |

An elapsed value displayed as `0.0000` means recovery met the declared
criterion at the first sampled state after the schedule returned to 4°.
Right-censoring is valid evidence, not a solver failure. No aesthetic or
turbulence-quality threshold is inferred from these measurements.

## PR #2 dispositions

The twelve unresolved CodeRabbit findings from PR #2 were rechecked against
current code and closed as follows:

1. Julia canonical solid-mask axis mapping: fixed in `61b2ad4` with a
   rectangular warm-switch regression.
2. Python Stable Fluids mutable MAC-limit transaction: fixed in `61b2ad4`.
3. Rust canonical-v2 identity and strict parsing: fixed in `4adda1a`.
4. PCG32 Float32 conversion can rarely round to `1.0`: valid, explicitly
   deferred to Revision 6 because changing it changes deterministic streams.
5. Rust non-finite `control_at`: fixed in `61b2ad4`.
6. TypeScript PIC/FLIP periodic swept endpoint: fixed in `61b2ad4`.
7. Host Cargo versus WASM verification wording: clarified in the repository
   README by this documentation closure.
8. Planning-retry `expected_steps`: consumed and asserted by all four
   implementations in `942fce1`.
9. Missing full-history fidelity evidence: added in `3d72a0e`.
10. Fidelity schema case and threshold integrity: hardened in `942fce1` and
    extended for the scheduled erratum in `3d72a0e`.
11. Parallel geometry sample lengths: validated by the specification tool and
    all four consumers in `942fce1`.
12. Acceptance-cell log traversal and symlink escape: rejected before digest
    validation in `942fce1`.

The PCG32 disposition is deliberate: Revision 5 vectors remain authoritative.
Revision 6 may define a corrected conversion and new conformance vectors; it
must not silently rewrite the accepted Revision 5 stream.
