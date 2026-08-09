# FoilBench contract suite

The machine-readable manifest [contract-version.json](contract-version.json)
names the normative documents and schemas for the current contract revision.
This page is the human routing guide; it does not duplicate their requirements.

## Authority by subject

| Subject | Authoritative document |
| --- | --- |
| Solver identifiers, capabilities, operations, reports, and failure vocabulary | [Flow solver contract](solver-contract.md) |
| Numerical stages required by each named solver family | [Solver repertoire contract](solver-repertoire-contract.md) |
| Successful-step, import, stability, convergence, and rollback criteria | [Solver validity contract](solver-validity-contract.md) |
| Interactive commands, ownership, recovery UX, visible tracers, diagnostics, and snapshots | [Interactive viewer contract](interactive-viewer-contract.md) |
| Canonical serialized field semantics and layout | [Canonical state](canonical-state.md) |
| Scenario, result, manifest, matrix, and transcript syntax | The JSON schema named in [contract-version.json](contract-version.json) |
| Timed-run procedure and measured fidelity meanings | [Benchmark methodology](../docs/benchmark-methodology.md) |
| Exact cross-language examples and thresholds | [Conformance fixtures](conformance/README.md) |

Cross-references explain how the pieces interact; they do not create a second
authority. For example, the viewer contract decides when a frontend may
attempt recovery, while the solver validity contract decides whether the
underlying numerical operation succeeded.

## Revision discipline

Breaking semantic changes increment `revision` in
[contract-version.json](contract-version.json). Compatible optional
capabilities may retain the revision when the manifest permits them. A
contract amendment should:

1. update the authoritative document rather than only an implementation or
   acceptance report;
2. update cross-references in affected contracts, including the original
   interactive viewer contract when observable behavior changes;
3. add or revise language-neutral fixtures and schemas where an executable
   threshold or artifact shape is required;
4. run conformance in every implemented language; and
5. record implementation deviations explicitly until they are reconciled.

The Phase 2 revision-2 documents intentionally precede their implementation
reconciliation. The contract review occurs first; language changes and new
fixtures follow only after that review is accepted.
