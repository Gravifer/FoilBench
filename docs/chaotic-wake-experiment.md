# Accepted Phase 1 chaotic-wake extension

This work began as a provisional excursion isolated on
`codex/chaotic-wake-experiment` and was accepted into the completed Phase 1
reference on 2026-08-01. It remains opt-in, does not change the robust default
transport, and makes no claim of three-dimensional turbulence. Its target is
deterministic, irregular 2D separated flow with a resolved range of
interacting scales. The original coherent alternating vortex street remains
sufficient for solver acceptance; this extension records an overachievement,
not a requirement for the basic three-solver acceptance bar. Implementations
that declare support for the optional chaotic-wake extension are nevertheless
required by Revision 4 to reproduce its shared qualitative classification.

## What changed

The Stable Fluids solver gained two opt-in transport paths:

- MacCormack transport directly on the staggered MAC faces, avoiding the
  dissipative cell/face reconstruction used by the reference path.
- Explicit midpoint RK2 transport of the MAC fields using a skew-symmetric
  convective operator. The skew form reduces artificial kinetic-energy
  creation, while a maximum CFL of 0.4 replaces semi-Lagrangian unconditional
  stability.

The experimental scenario uses the second path at `Re=10000`, 35 degrees of
attack, and the compact 160×96 grid. There is no vorticity confinement,
stochastic forcing, hard-coded vortex, or time-dependent inlet perturbation.
Because this intentionally coarse immersed-boundary case permits strongly
separated motion, it uses a pressure tolerance of `1e-4` and an
experiment-local MAC divergence cap of `8.0`. The tighter pressure solve keeps
the independently implemented Python and Julia projections in the same
localized-residual regime despite their different preconditioners. The
divergence envelope covers both direct and canonical-reconstructed paired
trajectories. The ordinary solver-validity limit remains `1e-4`; the larger
cap prevents a finite, rolled-back visualization trajectory from being
mistaken for a general validation case.

Run it with:

```powershell
uv run --project implementations/python foilbench-py view scenarios/airfoil/chaotic-experimental.json --solver stable-fluids
```

Enable the vorticity layer with `V`. The multiscale wake takes several
simulated seconds to develop; the foil remains draggable.

In either native frontend, `[` selects the robust MacCormack transport and
`]` selects skew-RK2 while Stable Fluids is active. The overlay reports the
active `adv=` mode. This live selection survives recovery and warm switching;
`R` restores the scenario's configured mode. The same keys continue to adjust
PIC/FLIP blending when that solver is active and have no effect on LBM.

Use `-` and `+` to move Reynolds number down or up in quarter-decade steps,
and `0` to return to `Re=10000`. A fresh 4-degree run becomes near-laminar by
`Re=500–1000`; lowering angle alone at `Re=10000` remains visibly unsteady.
The current flow is preserved when Reynolds changes, so a developed wake
relaxes rather than disappearing immediately.

The experimental viewer starts with a four-cell crop on each edge. This hides
the far-field vorticity sheet created where the finite-domain boundary condition
meets the differentiated display field. The solver, canonical state, and all
diagnostics still use the complete domain; this is presentation cropping, not
deletion of inconvenient numerical data. Press `C` to toggle immediately
between the cropped student view and the full diagnostic domain; the overlay
reports `view=cropped` or `view=full`.

The default airfoil scenario exposes the same four-cell crop through `C`, but
starts in the full view. `viewer_crop_cells` defines the available inset while
`viewer_crop_default` independently selects its initial state in both native
frontends.

## Evidence

Long-horizon probe statistics use the final eight simulated seconds unless
noted otherwise.

| Case | Spectral entropy | Off-peak power | Enstrophy CV | Highest-k power |
|---|---:|---:|---:|---:|
| Phase 1 transport, Re 1000, 25°, 160×96 | 0.159 | 1.1% | 3.2% | — |
| Direct-face MacCormack, Re 10000, 35°, 160×96 | 0.201 | 11.7% | 12.0% | 0.00045% |
| Skew RK2, Re 10000, 35°, 160×96 | 0.272 | 12.7% | 21.8% | 0.102% |
| Skew RK2, Re 10000, 35°, 240×144, six-second window | 0.166 | 12.6% | 23.5% | 0.044% |

The shorter refined window has coarser frequency resolution, so its entropy is
not directly comparable; the off-peak fraction and enstrophy variability do
persist. Refinement decreases the highest-wavenumber fraction rather than
causing a Nyquist pile-up. The fitted intermediate vorticity-power slope
changes from approximately -0.91 to -1.37.

A corrected paired-trajectory experiment applies a deterministic,
divergence-free perturbation of `1e-4 U` after giving both solvers identical
projection histories. On the 160×96 skew-RK2 case, wake-field separation grows
by approximately 18,800 times over twelve simulated seconds. A log-linear fit
over 345 samples gives a finite-time exponent of 1.15 and `R²=0.974`. The same
test on the semi-Lagrangian/direct-face candidate showed only 1.8 times growth
and a poor `R²=0.49`. This is strong evidence of deterministic chaos, though it
is not an asymptotic Lyapunov-exponent calculation.

The 160×96 skew-RK2 run took about 24.5 wall seconds for twelve simulated
seconds on the Phase 1 test machine, or roughly 34 ms per requested display
step. The refined 240×144 run took about 84 wall seconds for ten simulated
seconds.

## Negative results and caveats

- Raising Reynolds number from 1000 to 10000 or attack angle to 45 degrees on
  the original transport path strengthened a periodic street but did not make
  it chaotic.
- A wider reference canvas increased mode interaction but still produced only
  weak perturbation growth under direct-face MacCormack transport.
- Removing the MacCormack limiter did not increase broadband content and is
  therefore not retained.
- This remains a coarse immersed-boundary visualization solver. Boundary
  stair-stepping, numerical viscosity, open-boundary influence, and grid
  convergence of detailed statistics have not been eliminated.
- The flow is chaotic in the numerical 2D sense. It has no vortex stretching
  and should not be presented as quantitatively faithful 3D turbulence.

The repeatable measurement tools are `experiments/chaotic_wake_sweep.py` and
`experiments/chaos_sensitivity.py`. Their generated JSON and PNG artifacts are
kept under the gitignored `results/chaotic-wake/` directory.
