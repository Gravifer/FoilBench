# FoilBench static browser lab

FoilBench has two browser presentations with deliberately different jobs.

The TypeScript reference viewer remains the compact parity surface used while
comparing Python, Julia, TypeScript, and Rust/WASM behavior. The static browser
lab is a separate Svelte presentation intended for students and exploratory
play. Both use the same TypeScript worker protocol, latest-snapshot ownership,
Three.js scene controller, scenario validation, tracers, diagnostics, recovery
logic, and Rust/WASM adapter.

The released lab is available at
[https://gravifer.github.io/FoilBench/](https://gravifer.github.io/FoilBench/).
The compact TypeScript presentation remains a dev viewer rather than a
superseded or legacy interface: it is still the tighter parity and diagnostic
surface. For a user-facing explanation of presets, solvers, controls, tracer
modes, readouts, and simulation limits, see the
[browser lab guide](web-lab-guide.md).

## Runtime architecture

The lab is a static single-page application. A static file server supplies
HTML, CSS, JavaScript, fonts, scenario presets, the scenario schema, and the
Rust WebAssembly module. There is no application server, API, account, or
remote simulation process.

The browser supports two numerical backends:

- TypeScript is the current default and the faster browser path on the
  development machine.
- Rust/WASM is built from the same core as Rust/native and remains selectable
  for direct comparison.

Warm switching is available among solver families within one backend. Changing
backend is intentionally a cold restart: it preserves the authoritative foil
pose, selected Reynolds number, and applicable tuning, but resets physical
time, solver-private history, and tracer paths. The interface reports the
restart rather than presenting it as a state-preserving conversion.

## Playing with the lab

Drag the foil in the scene to change its angle of attack. Presets and solver
controls live in the collapsible controls panel; pause and reset use the
transport strip in the header. The interface exposes Stable Fluids, D2Q9 TRT
LBM, and blended PIC/FLIP under both browser backends.

Keyboard controls remain aligned with the dev viewers: `1/2/3` select the
solver, `Space` pauses, `R` resets, `+/-/0` adjust or reset Reynolds number,
`[/]` tune the active solver, and `V/T/C` toggle vorticity, tracer mode, and
diagnostic cropping. Modified shortcuts and keystrokes directed at interactive
HTML controls are ignored so ordinary browser commands such as copy continue
to work.

The SPA renders trails without point heads and weights their brightness by
history age and local speed. **View → Trail blending** compares three
presentation-only treatments on the same flow state: Normal is the restrained
source-over default, Additive accumulates overlapping light more strongly, and
Screen provides gentler density emphasis. Switching among them does not
restart the solver, reseed tracers, or change artifacts, and all three use the
same trail geometry, shader, and rendering pass. The compact dev viewers keep
their reference tracer presentation.

The controls panel also exposes an in-app lab guide that distinguishes solver
method from execution engine, display from material tracers, and physical
controls from presentation-only trail blending. It links to the complete
[browser lab guide](web-lab-guide.md) and records the simulation's deliberately
two-dimensional pedagogical limits. Chromium remains the primary browser test
surface; lightweight Firefox and WebKit production-artifact smokes cover both
the TypeScript and Rust/WASM engines without multiplying the full interaction
suite.

## Development

From the repository root:

```shell
just web-view
```

The development lab runs at `http://127.0.0.1:4175/`. The existing reference
viewer remains available through `just ts-view`.

To reproduce the GitHub Pages-shaped output without deploying it:

```shell
just web-build
just web-preview
```

The output is written to `apps/web/dist/` and uses the
repository base path `/FoilBench/`. Publication is release-gated: ordinary
pushes and pull requests never update the live site, prereleases are
publish-only, and only a newer stable release promotes to Pages. See the
[web release and deployment guide](web-release.md) for the dual manual/tag
workflow, artifact identity, checksums, and failure behavior.

## Presentation policy

The lab uses semantic 3Blue1Brown-inspired colors rather than Tailwind's
default hue families. CMU Serif, CMU Sans Serif, and CMU Typewriter Text are
self-hosted from an SIL-OFL package. Future localization reserves Noto Serif
CJK SC, Noto Sans CJK SC, and Sarasa Mono SC as the CJK companions, but this
stage ships English copy only and does not bundle the much larger CJK fonts.

The lab is responsive and supports touch dragging. Desktop and landscape
tablet remain the preferred layouts; phones retain the complete simulation and
move the controls into a drawer.
