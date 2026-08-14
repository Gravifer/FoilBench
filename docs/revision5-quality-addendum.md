# Revision 5 quality and errata closure

This addendum records a non-semantic quality pass over accepted Revision 5.
The numerical thresholds, artifact formats, solver repertoire, contract
identifier, accepted date, and PCG32 streams are unchanged.

The implementation commit under test was
`b581457fbc3061a872281b01463b9514dd40d002`. Documentation was committed
afterward and does not alter the tested implementation.

## Verification

The ordinary non-representative verifier was run with:

```powershell
pwsh -NoProfile -File tools/verify.ps1
```

It passed specification/schema validation, Ruff, strict Pyright, 212 Python
tests, the complete Julia package tests and GLMakie load check, TypeScript
strict checks and its complete test suite, the Vite production build, and both
production Chromium smokes. The run then caught a test-only Rust Clippy
float-comparison warning. After that assertion was corrected, Rust formatting,
Clippy, all 66 native tests, and the real `wasm32-unknown-unknown` target check
passed in a focused tail rerun. The previously accepted
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
`results/revision5-quality/b581457fbc3061a872281b01463b9514dd40d002/scheduled-fidelity/`.
The validated configuration digest is
`b2375cad8ccc93a7917533ac74969b177c71aa9df5cc6d69c57f59f33f7d7d65`.
The first validation pass correctly rejected Julia's serialized Float32
`output_dt` as lexically unequal to the source JSON value. Commit `1967153`
made nested numeric identity comparison precision-aware; it then validated the
already-generated cells without rerunning any solver.

| Producer | Solver | Mixing index | Recovery | Elapsed (s) |
| --- | --- | ---: | --- | ---: |
| Python/native | Stable Fluids | 0.0121255 | right-censored | 4.0000 |
| Python/native | D2Q9 LBM | 0.0135943 | observed | 0.0000 |
| Python/native | PIC/FLIP | 0.0407701 | observed | 1.4500 |
| Julia/native | Stable Fluids | 0.0123494 | right-censored | 4.0000 |
| Julia/native | D2Q9 LBM | 0.0136331 | observed | 0.0000 |
| Julia/native | PIC/FLIP | 0.0408948 | observed | 1.7500 |
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

1. Julia canonical solid-mask axis mapping: fixed in `62aaa30` with a
   rectangular warm-switch regression.
2. Python Stable Fluids mutable MAC-limit transaction: fixed in `62aaa30`.
3. Rust canonical-v2 identity and strict parsing: fixed in `b161673` and
   tightened for canonical payload names in `0976585`.
4. PCG32 Float32 conversion can rarely round to `1.0`: valid, explicitly
   deferred to Revision 6 because changing it changes deterministic streams.
5. Rust non-finite `control_at`: fixed in `62aaa30`.
6. TypeScript PIC/FLIP periodic swept endpoint: fixed in `62aaa30`.
7. Host Cargo versus WASM verification wording: clarified in the repository
   README by this documentation closure.
8. Planning-retry `expected_steps`: consumed and asserted by all four
   implementations in `c8d3b9e`.
9. Missing full-history fidelity evidence: added in `46c407e` and regenerated
   with exact event-aligned baseline semantics at `b581457`.
10. Fidelity schema case and threshold integrity: hardened in `c8d3b9e` and
    made case-roster-exact in `0bd5f55`.
11. Parallel geometry sample lengths: validated by the specification tool and
    all four consumers in `c8d3b9e`.
12. Acceptance-cell log traversal and symlink escape: rejected before digest
    validation in `c8d3b9e`.

## Post-rebase review closure

The rebased branch received a second blind integration review. Its actionable
findings were closed by exact scenario/control-history binding for scheduled
evidence, precision-aware semantic identity comparison, exact accepted target
rosters, event-aligned baseline sampling, strict recovery diagnostics, fixed
canonical payload names, a truthful Rust 1.86 MSRV with a dedicated CI job,
Rust 1.96 pinning in representative CI, and locked Cargo resolution in the
TypeScript WASM builder. Generated evidence is accepted only for its recorded
implementation commit and configuration digest.

The PCG32 disposition is deliberate: Revision 5 vectors remain authoritative.
Revision 6 may define a corrected conversion and new conformance vectors; it
must not silently rewrite the accepted Revision 5 stream.
