# Shared conformance fixtures

These fixtures freeze small, language-neutral semantics that every FoilBench
implementation must reproduce before solver comparisons are meaningful.

- `pcg32.json` fixes integer streams and the exact Float32 conversion.
- `naca2412.json` fixes surface, signed-distance, containment, and normal
  samples for a transformed NACA 2412 foil.
- `canonical-state-f32/` is a complete little-endian canonical state with C
  semantic order `z y x component`.

Python is the semantic reference used to generate the fixtures, but Python is
not required to read them. Regenerate deliberately from the repository root:

```powershell
uv run --project implementations/python python tools/generate_conformance.py
```

Both Python and later-language tests must read the committed fixtures. A
fixture change therefore requires an intentional cross-language contract
update rather than an implementation-local snapshot rewrite.
