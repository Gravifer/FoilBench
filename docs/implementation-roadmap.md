# Implementation roadmap

## Phase 1: Python reference — complete

**Status:** Completed on 2026-07-31 and enhanced through the accepted
chaotic-wake excursion on 2026-08-01. The requirement-by-requirement evidence
is recorded in [Phase 1 acceptance](phase1-acceptance.md).

- Typed shared contracts and geometry.
- Stable Fluids, D2Q9 TRT LBM, and blended PIC/FLIP.
- Native ModernGL viewer with direct within-Python warm switching.
- Portable benchmark and fidelity artifacts.

The original acceptance audit completed strict Pyright and Ruff checks, all 88
then-current Python tests, finite matched runs for all three solvers at 32 and
48 cells per chord, direct warm-switch coverage, graceful fresh recovery, and
a responsive latest-snapshot viewer. The 32-cells-per-chord preview sustained
double-digit solver updates per second for all three solvers in developed
fixed stall. The accepted extension raises the automated suite to 105 tests
and adds online Reynolds control, diagnostic cropping, rapid-motion recovery,
and fail-fast pressure-solver safety.

The completed 2026-08-02 shared viewer-contract reconciliation and
maintenance pass raise the Python suite to 133 tests and align timestamped
drag controls, transactional switching, typed and bounded recovery, tracer
continuity, presentation state, ordered commands, snapshot semantics, and
interactive performance accounting with Julia.

### Accepted wake behavior

The compact Stable Fluids preview produces separated shear-layer roll-up,
recirculation, and an unsteady transverse wake. A coherent alternating vortex
street satisfies the Phase 1 pedagogical requirement. It demonstrates the
onset and persistence of separated unsteady flow without claiming to reproduce
three-dimensional turbulence. That bar is unchanged: later implementations do
not need to reproduce the optional chaotic extension to satisfy basic Phase 1
wake acceptance.

An opt-in skew-symmetric RK2 transport scenario additionally produces a
repeatable, irregular multiscale 2D wake. Probe spectra, enstrophy variation,
resolution comparison, and paired-trajectory sensitivity support describing
it as deterministic numerical 2D chaos. This extension does not replace the
more robust default transport and makes no claim of three-dimensional vortex
stretching or predictive stall fidelity; it is an overachievement beyond the
required coherent-vortex-street behavior.

The benchmark records a downstream transverse-velocity probe, shedding
frequency, Strouhal number, RMS fluctuation, and the fraction of spectral power
in the dominant peak. These values characterize coherent and broadband wakes;
they do not assign either an automated visual-quality score. Subcell boundary
refinement remains a future fidelity improvement, not a Phase 1 blocker.

## Phase 2A: Julia

**Status:** Completed on 2026-08-01 on `codex/phase2a-julia`. Detailed evidence
is recorded in [Phase 2A acceptance](phase2a-acceptance.md).

- Independent Julia package with type-stable contracts, PCG32, NACA geometry,
  x-major shared numerics, canonical state I/O, and schema-valid artifacts.
- Stable Fluids, D2Q9 TRT LBM, and blended PIC/FLIP with runtime Reynolds
  changes, deterministic seeds, requested-time behavior, and explicit 2D
  capabilities.
- Native GLMakie frontend with passive tracers, batched path history,
  vorticity, diagnostic crop, context-sensitive Stable/PIC tuning, all
  Python-equivalent controls, and simulation work isolated from rendering.
- All six directed warm swaps, conversion-transient reporting, structured
  rejection with source retention, time-preserving forced recovery,
  full tracer reseeding with continuity generations, a Reynolds circuit
  breaker, and the self-releasing rapid-drag pose-only tier.
- Native `view`, `bench`, `compare`, and `describe` commands; matched fidelity,
  component profiling, portable JSON/CSV/snapshot output, and optional
  chaotic-wake sweep and paired-trajectory experiments.

At Phase 2 closure the Julia suite contains 760 passing assertions. On the
development machine, the `160 x 96` preview gate measured 19.82 Stable Fluids,
40.41 LBM, and 14.60 PIC/FLIP solver steps per second after warm-up. Julia
remains a peer: it reads shared specifications and writes shared artifacts but
never loads Python code or Python solvers.

### Post-reconciliation QA queue

A parallel read-only QA pass on 2026-08-02 found no P0 defect. Its following
high-priority implementation-correctness findings were completed without
requiring additional policy decisions:

- Julia canonical `.npy` storage now agrees with its manifest and is verified
  with a cross-language, nonsymmetric-array fixture;
- Julia scenarios and benchmark results now use the complete shared
  schemas rather than partial hand-written checks;
- Python and Julia pressure/projection breakdowns now become typed numerical
  failures, while Python LBM and PIC/FLIP reject non-finite steps before they
  can return successful reports;
- command-path failures terminate safely, and every accepted warm switch is
  published at its exact completed validation-step boundary before ordinary
  evolution resumes;
- recent failure evidence survives rejected and no-op solver switches;
  and
- interactive `sim/wall` accounting remains active through continuous dragging
  and presentation commands so pacing, publication, tracer, and diagnostic
  costs remain included.

The focused implementation commits are `2b550b4`, `9fda478`, `ffc3bb6`,
`c918572`, `70ffc35`, and `3df6e11`.

The subsequent medium-priority closure pass is also complete:

- the first successful step after reset or recovery refreshes every displayed
  solver diagnostic before leaving `warming`;
- Julia command and wake channels no longer impose bounded backpressure while
  coordination locks are held, with a four-producer regression crossing the
  former queue bound;
- solver import APIs in both languages return accepted or rejected
  `ImportOutcome` values directly for expected incompatibility and numerical
  reconstruction failures rather than classifying exception messages; and
- the first ordinary step after a published warm switch retains an explicit
  post-import marker, which is cleared only by a successful step and produces
  `stage=post-import` recovery telemetry on failure.

The focused closure commits are `4e49007`, `eaf60f9`, `c65c2bf`, and
`d80974b`.

### Former user-attention queue

Revision 2 settled the Phase 2 policy questions that affected observable
conformance: rejected warm-import fallback is reason-classified and attempted
at most once; diagnostics remain presentation-cadenced; and a shared
language-neutral viewer transcript now exercises command barriers. Exact drag
cap, smoothing, and hysteresis constants remain deliberately unfrozen, while
the contract fixes their observable safety and pose semantics. Pixel-level
visual closeness and a broader cross-platform GPU stress matrix remain useful
future experiential work, not Phase 2 acceptance gates. The remaining scope is
recorded in [the interactive viewer contract](../spec/contracts/interactive-viewer-contract.md#open-decisions).

## Phase 2B: TypeScript

**Status:** Completed. Automated implementation and acceptance were revalidated
on 2026-08-03, TypeScript reconciliation completed on 2026-08-09, and the
three-language Revision 2 closure was accepted on 2026-08-11.

Phase 2B formalized the shared solver, scenario, benchmark-matrix,
canonical-manifest, result, and PCG32 contracts. It added an independent
strict TypeScript implementation with typed-array Stable Fluids, D2Q9 TRT
LBM, and blended PIC/FLIP solvers; a Web Worker simulation owner; a Three.js
viewer; and Chromium-native benchmarks. TypeScript shares artifacts and
semantic fixtures with Python and Julia but never loads their code or solvers.

Completed implementation milestones are:

1. shared contract hardening and TypeScript conformance foundations;
2. Stable Fluids and the worker/Three.js viewer;
3. D2Q9 TRT LBM and blended PIC/FLIP;
4. all directed warm swaps at low and high attack angles, graceful recovery, online Reynolds control,
   tracer continuity, and presentation parity;
5. Chromium benchmark artifacts and offline three-language comparison; and
6. strict TypeScript, cross-language fidelity, browser interaction, and
   `160 x 96` double-digit warmed-step acceptance.

The implementation supplies all three independent typed-array solvers,
MacCormack/semi-Lagrangian/skew-RK2 Stable Fluids transport, TRT LBM with
moving-wall and open-domain treatment, four-particles-per-cell blended
PIC/FLIP, a latest-only Web Worker owner, Three.js paths and vorticity,
transactional switching, classified recovery, canonical snapshots, and a
Chromium-native benchmark runner. The matched validation suite, production
build, browser interaction smoke test, and snapshot interoperability checks
pass. Python successfully loads the TypeScript C-order canonical artifacts.

An extensive parallel QA pass after the initial acceptance record found that
several tests and implementation shortcuts were weaker than those claims. The
resulting corrective sequence is complete:

- canonical imports validate full metadata and array payloads and roll back
  atomically on failure (`da7feb8`);
- LBM uses exact requested physical intervals, a true D2Q9 Mach cap, adaptive
  temporal scaling, both interpolated bounce-back branches, channel-wall
  reflection, and zeroed solid-cell canonical velocity (`3ba149d`, `0314c7e`);
- PIC/FLIP now performs the conventional particle-to-MAC transfer, projection,
  PIC/FLIP update, and RK2 particle advection cycle with deterministic
  occupancy maintenance and transactional rollback (`09288e8`);
- the Web Worker coalesces pointer poses, permits only one transferable
  snapshot in flight, publishes latest-only revisions, isolates presentation
  failures, and reports owner-loop rather than solver-only timing (`7773965`);
- quantitative Poiseuille and NACA 0012 checks, all viewer-level warm-swap
  directions, wake spectra, and excursion recovery evidence replace the
  earlier superficial coverage (`0314c7e`, `a408952`); and
- benchmark artifacts now carry a self-contained matched-run identity across
  all three languages (`d304a52`).

The final Phase 2 verifier records 103 passing TypeScript tests plus the live
Chromium smoke test, 760 Julia assertions plus the GLMakie environment check,
and 149 Python tests with strict Ruff and Pyright.

The reproducible preview-gate matrix passed three repetitions for every solver
at `160 x 96` after the revision 2 validity instrumentation. Observed median
step ranges on the development machine were `12.8–18.5 ms` for Stable Fluids,
`94.8–97.2 ms` for LBM, and `77.1–96.8 ms` for PIC/FLIP. See
[Phase 2B acceptance](phase2b-acceptance.md) for commands and evidence.

The TypeScript revision 2 reconciliation adds frozen-field midpoint-RK2
visible tracers with explicit lifecycle semantics and authoritative current
foil-pose placement; explicit fresh-solver restart semantics; state revisions
and structured failure evidence; bounded iterative and substep work; exact
requested-time checks; and solver-family validity evidence for Stable Fluids,
LBM, and PIC/FLIP. It also includes moving-wall work in stability selection and
uses the wall-relative response for PIC/FLIP collision handling. Python and
Julia now satisfy the same transaction, validity, tracer, viewer, and artifact
semantics. The shared solver-validity and tracer-lifecycle fixtures are
executable in all three languages, making Revision 2 the first accepted
three-language baseline.

The Revision 3 QA closure below supersedes it for new implementation work
without erasing that historical milestone.

A coherent alternating vortex street remains sufficient wake behavior. The
skew-RK2 chaotic-wake sweep and paired-trajectory experiment are optional
post-acceptance enhancements.

### Phase 2 closure

The closure sequence through `389f180` reconciled exact requested-time and
transactional solver behavior, periodic MAC projection, direct face-centered
PIC/FLIP transfer, viewer command and recovery semantics, benchmark evidence,
artifact identity, and executable Revision 2 fixtures. Comparable result and
transcript artifacts carry `foilbench-phase2-v1` revision 2; comparers validate
their schemas and reject mismatched physical identities.

On 2026-08-11, `pwsh -NoProfile -File tools/verify.ps1` passed the complete
Python, Julia, and TypeScript verification path, including GLMakie environment
loading, the Vite production build, and live Chromium interaction. Chromium is
the required Phase 2B browser; Firefox, Safari, broader GPU matrices, exact
drag constants, and visual-closeness criteria are compatibility or tuning
follow-ups rather than hidden conformance latitude.

### Revision 3 QA closure

**Status:** Accepted on 2026-08-12 and superseded by Revision 4 on 2026-08-13.

The high-effort closure review found no P0 defect and converted its actionable
findings into focused repairs:

- artifact comparers now compare decoded numeric meaning rather than JSON
  number spelling or object order, and successful artifacts bind final fields,
  reports, and diagnostics to explicit solver-state revisions;
- shared validity fixtures enforce quantitative limits, bounded iterative
  solves, exact LBM time mapping, atomic rollback, periodic face diffusion,
  post-step motion evidence, and representable runtime Reynolds changes;
- recoverable post-step substep-planning misses now roll back and retry the
  same requested interval internally, and the published 160×96 chaotic-wake
  startup is exercised without substituting a smaller test grid;
- canonical exporters zero solid-cell velocity consistently, LBM importers
  ignore finite density inside the authoritative solid, and TypeScript validates
  precision and control pose before import;
- native MAC diagnostics use face divergence and wall-relative interface
  leakage, while LBM distinguishes zero cut-link through-flux from adjacent-cell
  normal speed;
- TypeScript control-plane status can progress during render backpressure
  without relabeling an older physical frame, and Python initial tracers honor
  the authoritative initial foil pose; and
- fresh Python and Julia recovery retains the scenario's declared initial
  condition, while TypeScript LBM now retains convective outlet history like
  its peers.

The final gate passed strict Ruff and Pyright with 157 Python tests, 873 Julia
assertions plus the GLMakie environment load, and 113 TypeScript tests plus the
Vite production build and live Chromium interaction. The closure commits begin
at `ba2d93b` and end at `fdafea6`.

### Revision 4 representative-parity closure

**Status:** Accepted on 2026-08-13; Revision 4 is the implemented
three-language baseline for Phase 3.

Revision 4 follows a full-size review which showed that tiny uniform and short
startup substitutes could pass while advertised solver and interchange paths
failed. It requires:

- solver-family-wide transactional retry of recoverable temporal planning
  misses, including PIC/FLIP and admissible LBM rescaling;
- pressure acceptance against the shared algebraic relative residual rather
  than an implementation-private update-size proxy;
- an independently emitted three-language benchmark matrix accepted by every
  comparer under declared-precision identity semantics;
- fresh switch fallback validation through one tentative destination step
  before the valid source is replaced;
- full-size startup, scheduled 14- and 25-degree checkpoints, preview gates,
  and evolved warm-switch/fallback coverage; and
- explicit participation and full-duration qualitative classification for the
  optional skew-RK2 chaotic-wake extension.

Revision 4 also closes viewer observability gaps around typed failure reasons,
metric invalidation after paused solver mutations, and tracer lifecycle
counters and precedence. The shared representative gate is
`spec/conformance/fullsize-acceptance.json`. The root verifier may keep a fast
default tier, but contract acceptance must run and record the representative
tier separately rather than implying that the fast tier covered it.

The authoritative `just verify-representative` run was refreshed after the
final QA corrections and passed at implementation commit
`cdd9ba599f340f0f1510b650683a500000935d53`. It included the exact
Python/Julia/TypeScript producer roster, all native comparers, all directed
warm-switch and fresh-fallback transactions at 14 and 25 degrees, and all
declared full-duration chaotic-wake cases. Every native canonical reader also
loaded all nine producer/solver snapshots and exercised all three native
destination importers. A full-resolution 0.1-second
preflight first proved symmetric canonical reconstruction: the three realized
initial wake RMS separations were all approximately `8.326e-6` for requested
epsilon `1e-4`. See [Revision 4 acceptance](revision4-acceptance.md).

### Deferred Phase 3 contract decisions

Two design questions are deliberately recorded for Phase 3 kickoff rather
than silently decided by an implementation:

- canonical manifests currently identify domain and pose but not the NACA
  geometry itself; decide whether cross-geometry import is supported or add a
  geometry fingerprint before claiming rejection via `incompatible_geometry`;
- all three LBM implementations provide an outlet/transverse sponge, but its
  numerical widths and strengths remain implementation constants; decide
  whether Rust parity needs shared scenario-level sponge parameters before
  exposing those values as user controls.

Exact drag cap/smoothing constants and cross-renderer visual-closeness criteria
remain the experiential open decisions already recorded in the
[interactive viewer contract](../spec/contracts/interactive-viewer-contract.md#open-decisions).

## Phase 3: Rust and WASM

**Status:** Kickoff in progress against accepted Revision 4 and the proposed
Revision 5 Phase 3 contract.

The Phase 3 workspace separates `foilbench-core` (no filesystem/browser),
`foilbench-native` (CLI, artifacts, benchmarks), and `foilbench-wasm`
(`wasm-bindgen` host boundary). The foundation currently includes PCG32,
typed scenario semantics, NACA geometry, canonical version 1/2 models, and the
solver trait. It deliberately advertises no Rust solver yet.

The prioritized kickoff sequence is:

1. stabilize proposed Revision 5 geometry, canonical identity, fidelity,
   boundary, producer-target, and conformance-inventory semantics;
2. implement native 2D Stable Fluids, then D2Q9 TRT LBM, then blended PIC/FLIP;
3. establish native parity and artifacts before hosting the same core in the
   TypeScript simulation worker through coarse WASM calls;
4. accept Revision 5 only after required native/WASM target evidence passes.

CI is split accordingly: pull requests run language-native static/unit suites,
Rust format/lint/tests, and a production-dist browser smoke. Scheduled or
manual representative acceptance runs as at most twelve independent
language/gate cells. Aggregation requires the exact commit and configuration
digest for every cell; intermediate cells expire after seven days and the
aggregate after thirty days.

Pixel-level renderer matching is a permanent won't-do. D3Q19 and shallow
periodic 3D remain deferred until all three 2D Rust solvers pass parity.
