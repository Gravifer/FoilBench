# Acceptance roster and execution targets

Status: accepted normative component.

Revision 5 distinguishes an implementation from the environment in which it
runs. The required producer identity is the ordered pair
`implementation/execution_target`; neither field may be inferred from the
other. The accepted identities are:

- `python/native`;
- `julia/native`;
- `typescript/node` for headless solver, conformance, and chaotic-wake
  evidence;
- `typescript/browser-worker` for ordinary benchmark artifacts, scheduled
  fidelity, browser protocol, and preview evidence;
- `rust/native` for the complete numerical repertoire;
- `rust/wasm-browser` for the browser-facing Rust repertoire.

Revision 5 deliberately assigns TypeScript targets per evidence family. The
ordinary-artifact and scheduled-fidelity roster is Python/native,
Julia/native, TypeScript/browser-worker, and Rust/native. Headless startup,
scheduled-control, warm-switch, and chaotic-wake gates instead require
TypeScript/node, as fixed by `fullsize-acceptance-v2.json`. Rust/WASM additionally participates in
protocol, scheduled-control, deterministic-state, recovery, canonical,
all-directed warm-switch, sensitivity-preflight, preview, and production-dist
browser gates. One TypeScript target does not substitute for the other where a
gate names an exact target, and Rust/native never substitutes for
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
