# Shared conformance fixtures

These fixtures freeze small, language-neutral semantics that every FoilBench
implementation must reproduce before solver comparisons are meaningful.

- `pcg32.json` fixes integer streams and the exact Float32 conversion.
- `naca2412.json` fixes surface, signed-distance, containment, and normal
  samples for a transformed NACA 2412 foil.
- `canonical-state-f32/` is a complete little-endian canonical state with C
  semantic order `z y x component`.
- `canonical-state-f32-fortran/` is the same nonsymmetric state encoded with
  Fortran storage so readers cannot accidentally infer semantic axes from
  memory order.
- `viewer-basic.json` is the first deterministic interactive command
  transcript. All language harnesses consume the same monotonic input and
  compare semantic state rather than renderer text or frame counts.
- `tracer-lifecycle.json` fixes the frozen-field midpoint trajectory and the
  observable recycle, pose, continuity, and per-language determinism policy.
- `solver-validity.json` fixes the shared requested-time, bounded-motion,
  revision, rollback, and accepted-evidence probes for all three solver IDs.
- `vorticity-display.json` fixes authoritative-pose solid masking, robust
  fluid-only normalization, signed display range, and renderer sign/opacity
  meaning for the pedagogical vorticity underlay.

Revision 4 requires the shared tracer-lifecycle, solver-validity, and
vorticity-display fixtures above. Their normative meanings remain defined by
the interactive viewer and solver-validity contracts; the JSON files supply
executable values rather than creating another source of policy.

Tracer fixtures assert per-language deterministic replay, exact lifecycle
semantics, analytic integration tolerances, and statistically comparable
coverage. They do not require cross-language bit-identical tracer RNG draws,
positions, or paths.

PCG32 uses unsigned 64-bit state, multiplier `6364136223846793005`, and an
odd increment `(stream << 1) | 1`. Initialization starts at state zero,
advances once, adds the unsigned seed modulo `2^64`, and advances again.
Every subsequent state transition wraps modulo `2^64`; output uses XSH-RR
64/32. Scenario seeds are unsigned 32-bit integers.

Python is the semantic reference used to generate the fixtures, but Python is
not required to read them. Regenerate deliberately from the repository root:

```powershell
uv run --project implementations/python python tools/generate_conformance.py
```

Both Python and later-language tests must read the committed fixtures. A
fixture change therefore requires an intentional cross-language contract
update rather than an implementation-local snapshot rewrite.
