# Revision 4 acceptance

Revision 4 became FoilBench's accepted three-language baseline on 2026-08-13.
The implementation under test was commit
`3eba1818566e07a1dfa34f44194c95f2faf794ca`; the documentation activation was
committed afterward and did not alter executable behavior.

## Authoritative run

The single complete acceptance invocation was:

```powershell
just verify-representative
```

It completed successfully in 3,538.6 seconds. Generated, gitignored evidence
was written under `results/revision4-acceptance/20260813-015014/`. The evidence
manifest records the exact tested commit, contract revision, producer roster,
benchmark directories, preflight artifacts, and full chaotic-wake artifacts.

Runtime context:

- Razer Blade 17 (2022), Intel Core i7-12800H, 34.0 GB physical memory;
- Windows 11 Pro for Workstations, build 26200, 64-bit;
- Python 3.12.12, Julia 1.12.6, Node.js 24.18.0, and npm 12.0.2; and
- AC power connected for the accepted performance run. A preceding battery
  interval was rejected as externally power-limited evidence.

## Gate results

Strict Ruff and Pyright passed with 172 Python tests. Julia's native suite,
the GLMakie load/smoke path, and all representative gates passed. TypeScript
passed 126 Vitest tests, the Vite production build, live Chromium interaction,
and all representative gates.

The accepted 160 x 96 warmed preview rates were:

| Language | Stable Fluids | D2Q9 LBM | PIC/FLIP |
| --- | ---: | ---: | ---: |
| Python | 57.73 steps/s | 35.44 steps/s | 19.00 steps/s |
| Julia | 20.55 steps/s | 22.57 steps/s | 16.94 steps/s |
| TypeScript | 17.57 steps/s | 15.04 steps/s | 13.61 steps/s |

Every solver exceeded the unchanged 10 steps/s threshold. Startup,
scheduled-control checkpoints, all twelve directed warm swaps, and fresh
fallback success/rejection transactions at 14 and 25 degrees passed in every
language.

Each language independently emitted the complete smoke benchmark matrix.
Python, Julia, and TypeScript comparers all accepted the combined exact
three-producer roster with no missing matrix cells or physical-identity
mismatch.

## Paired-sensitivity correction and evidence

The acceptance run first executed a full-resolution 0.1-second preflight. Both
members of every pair imported the same exported canonical base through the
same reconstruction path; only the second state received the declared
perturbation. Both imports had to be accepted before measurement.

| Language | Requested epsilon | Realized initial wake RMS | Realized/epsilon |
| --- | ---: | ---: | ---: |
| Python | 1.0e-4 | 8.32596e-6 | 0.0832596 |
| Julia | 1.0e-4 | 8.32538e-6 | 0.0832538 |
| TypeScript | 1.0e-4 | 8.32624e-6 | 0.0832624 |

The preflight exact-roster validator passed before the 12-second trajectories
were allowed to run. The full sensitivities then reported amplifications of
34,659.5 (Python), 52,882.3 (Julia), and 52,336.3 (TypeScript), all from
comparable post-reconstruction separation rather than reconstruction error.

All twelve sweep cells (four cases in each of three languages) passed the
declared qualitative 2D wake thresholds. Julia's previously marginal
`Re=10,000`, 25-degree case produced enstrophy coefficient of variation
0.07364 against the 0.05 minimum after the native-face CFL corrections.
These results support deterministic irregular 2D wake behavior; they do not
claim three-dimensional turbulence or quantitative aerodynamic prediction.

## Result

Revision 4 satisfies the exact Python/Julia/TypeScript participation roster,
canonical interchange, representative solver and viewer behavior, fallback
transactions, performance floor, and optional chaotic-extension claim. Rust
and WASM may therefore use Revision 4 as their initial parity target.
