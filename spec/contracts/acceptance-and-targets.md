# Acceptance roster and execution targets

Status: accepted normative component.

Revision 5 distinguishes an implementation from the environment in which it
runs. The required producer identity is the ordered pair
`implementation/execution_target`; neither field may be inferred from the
other. The accepted identities are:

- `python/native`;
- `julia/native`;
- `typescript/node` for headless numerical evidence;
- `typescript/browser-worker` for browser protocol and preview evidence;
- `rust/native` for the complete numerical repertoire;
- `rust/wasm-browser` for the browser-facing Rust repertoire.

The native numerical roster is Python/native, Julia/native, TypeScript/node,
and Rust/native. Each independently emits ordinary benchmark, fidelity,
interchange, and chaotic-wake evidence. Rust/WASM additionally participates in
protocol, scheduled-control, deterministic-state, recovery, canonical,
all-directed warm-switch, sensitivity-preflight, preview, and production-dist
browser gates. A TypeScript browser-worker result never substitutes for a
TypeScript/node numerical cell, and Rust/native never substitutes for
Rust/wasm-browser.

Each reusable acceptance cell records the exact Git commit, a digest covering
every transitive fixture and configuration input, implementation, execution
target, gate, solver or case where applicable, declared thresholds, observed
measurements, and a digest of its log. Missing, duplicate, malformed, failed,
or stale cells reject the aggregate. Hosted CI records preview throughput but
does not enforce the development-machine absolute threshold for Rust/WASM.

Long sensitivity trajectories may run only after the corresponding symmetric
canonical reconstruction preflight succeeds. The full acceptance aggregate is
authoritative; exploratory partial comparisons remain available but cannot
activate the revision.
