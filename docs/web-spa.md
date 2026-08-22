# FoilBench static browser lab

FoilBench has two browser presentations with deliberately different jobs.

The TypeScript reference viewer remains the compact parity surface used while
comparing Python, Julia, TypeScript, and Rust/WASM behavior. The static browser
lab is a separate Svelte presentation intended for students and exploratory
play. Both use the same TypeScript worker protocol, latest-snapshot ownership,
Three.js scene controller, scenario validation, tracers, diagnostics, recovery
logic, and Rust/WASM adapter.

## Runtime architecture

The lab is a static single-page application. A static file server supplies
HTML, CSS, JavaScript, fonts, scenario presets, the scenario schema, and the
Rust WebAssembly module. There is no application server, API, account, or
remote simulation process.

The browser supports two numerical backends:

- Rust/WASM is the default production path.
- TypeScript remains an independent comparison implementation.

Warm switching is available among solver families within one backend. Changing
backend is intentionally a cold restart: it preserves the authoritative foil
pose, selected Reynolds number, and applicable tuning, but resets physical
time, solver-private history, and tracer paths. The interface reports the
restart rather than presenting it as a state-preserving conversion.

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

The output is written to `implementations/typescript/dist-web/` and uses the
repository base path `/FoilBench/`. Deployment is deliberately not enabled yet.

## Presentation policy

The lab uses semantic 3Blue1Brown-inspired colors rather than Tailwind's
default hue families. CMU Serif, CMU Sans Serif, and CMU Typewriter Text are
self-hosted from an SIL-OFL package. Future localization reserves Noto Serif
CJK SC, Noto Sans CJK SC, and Sarasa Mono SC as the CJK companions, but this
stage ships English copy only and does not bundle the much larger CJK fonts.

The lab is responsive and supports touch dragging. Desktop and landscape
tablet remain the preferred layouts; phones retain the complete simulation and
move the controls into a drawer.
