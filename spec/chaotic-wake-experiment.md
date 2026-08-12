# Chaotic-wake experiment contract

Status: proposed normative optional extension of `foilbench-phase2-v1`,
revision 4.

The Phase 2 acceptance bar remains a visibly unsteady vortex street. These
experiments are an overachievement track for deterministic numerical 2D
chaos; they do not claim three-dimensional turbulence or vortex stretching.

All implementations use Stable Fluids with `skew-rk2`, the cases in
`spec/conformance/chaotic-wake-cases.json`, a transverse-velocity probe 1.5
chords downstream of the foil pivot, and the result envelope in
`spec/chaotic-wake-result.schema.json`.

The sweep reports probe RMS, normalized spectral entropy, dominant and
broadband power fractions, 1/e decorrelation time, mean and coefficient of
variation of enstrophy, maximum speed, and a dimensionless small-scale
vorticity fraction. The latter may use implementation-native discrete
gradients and is a trend metric, not a cross-language equality target.

The paired sensitivity experiment starts two otherwise identical trajectories
and adds the same deterministic divergence-free streamfunction perturbation
to one canonical velocity field. It reports wake RMS separation, maximum
amplification, and a finite-time exponential fit. Float precision and solver
details can make exact trajectories diverge; parity means identical setup and
metric definitions, finite schema-valid output, deterministic repetition
within one implementation, and the same qualitative classification. An
implementation either declares participation in this extension and passes the
full-duration shared classification fixture, or declares the extension
unsupported. A completed but overwhelmingly narrow-band result may not be
reported as parity merely because it validates against the result schema.

No automated result from this extension is a visual-quality score. The raw
metrics and optional series remain available for human interpretation.
