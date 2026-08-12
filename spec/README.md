# FoilBench contract suite

The machine-readable manifest [contract-version.json](contract-version.json)
names the normative documents and schemas for the current contract revision.
This page is the human routing guide; it does not duplicate their requirements.

## Layout

```text
spec/
  README.md              human routing guide
  contract-version.json accepted revision and complete authority manifest
  contracts/             normative prose and semantic requirements
  schemas/               JSON syntax and structural validation
  conformance/           language-neutral fixtures and executable values
```

Only files named by `contract-version.json` are normative contract documents
or schemas. The `conformance_root` supplies shared examples and thresholds;
its README explains which contract owns each fixture's meaning.

Schema `$id` values are stable logical identifiers. They intentionally retain
their accepted Revision 4 URIs even when a schema's repository location moves
under `spec/schemas/`; consumers load the paths declared by the manifest and
must not infer a filesystem path from `$id`.

The words **must**, **should**, and **may** have their usual normative force
throughout the suite: **must** is required for conformance, **should** may be
deviated from only with a recorded justification and equivalent observable
behavior, and **may** denotes permitted variation. A document marked
`proposed` defines the candidate next contract but does not establish current
implementation conformance until the manifest's activation requirements are
met.

## Authority by subject

| Subject | Authoritative document |
| --- | --- |
| Solver identifiers, capabilities, operations, reports, and failure vocabulary | [Flow solver contract](contracts/solver-contract.md) |
| Optional skew-RK2 wake and paired-sensitivity evidence | [Chaotic-wake experiment contract](contracts/chaotic-wake-contract.md) |
| Numerical stages required by each named solver family | [Solver repertoire contract](contracts/solver-repertoire-contract.md) |
| Successful-step, import, stability, convergence, and rollback criteria | [Solver validity contract](contracts/solver-validity-contract.md) |
| Interactive commands, ownership, recovery UX, visible tracers, diagnostics, and snapshots | [Interactive viewer contract](contracts/interactive-viewer-contract.md) |
| Canonical serialized field semantics and layout | [Canonical state](contracts/canonical-state.md) |
| Scenario, result, manifest, matrix, and transcript syntax | The JSON schema named in [contract-version.json](contract-version.json) |
| Timed-run procedure and measured fidelity meanings | [Benchmark methodology](contracts/benchmark-methodology.md) |
| Exact cross-language examples and thresholds | [Conformance fixtures](conformance/README.md) |

Cross-references explain how the pieces interact; they do not create a second
authority. For example, the viewer contract decides when a frontend may
attempt recovery, while the solver validity contract decides whether the
underlying numerical operation succeeded.

## Recommended reading order

For a new implementation:

1. Read [the flow solver contract](contracts/solver-contract.md) for the public
   protocol and failure vocabulary.
2. Read [the solver repertoire](contracts/solver-repertoire-contract.md) and
   [solver validity](contracts/solver-validity-contract.md) together for each
   implemented family.
3. Implement [canonical state](contracts/canonical-state.md), then exercise the
   shared canonical fixtures in both C and Fortran storage order.
4. Implement artifacts and native comparison according to
   [benchmark methodology](contracts/benchmark-methodology.md).
5. Add the [interactive viewer contract](contracts/interactive-viewer-contract.md)
   after the native solver and state boundaries are stable.
6. Claim the optional [chaotic-wake extension](contracts/chaotic-wake-contract.md)
   only when its complete shared evidence can be produced.

Schemas and conformance fixtures should be consulted alongside each step,
not treated as substitutes for the semantic prose.

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

Phase 2 revision 2 was accepted on 2026-08-11 after the language-neutral
fixtures and schemas were made executable in Python, Julia, and TypeScript,
all comparable artifacts identified the contract, and the combined root
verifier passed. Revision 3 subsequently became the implemented baseline.
Revision 4 became the accepted Phase 3 baseline on 2026-08-13 after its
representative full-size, interchange, fallback, paired-initialization, and
optional-extension gates passed in Python, Julia, and TypeScript. Future
proposed revisions must repeat that activation sequence rather than claiming
conformance from prose alone.
