# FoilBench browser lab guide

The [FoilBench browser lab](https://gravifer.github.io/FoilBench/) is a toy
two-dimensional wind tunnel for exploring how an airfoil disturbs a flow. It
runs entirely in the browser: scenarios, solvers, visible tracers, rendering,
and recovery logic remain on the local device. Nothing is uploaded, and no
application server performs the simulation.

This guide explains what the controls do and, just as importantly, what the
result should and should not be taken to mean. For implementation and release
details, see the [browser-lab architecture](web-spa.md) and
[release guide](web-release.md).

## A five-minute tour

1. Start with **Dynamic stall**, **Stable Fluids**, and the default
   **TypeScript** engine.
2. Watch the scheduled angle move from 4° to 14°, then 25°, and finally back
   to 4°. Look for attached flow, separation, a disturbed wake, and delayed
   recovery.
3. Drag around the foil or use the angle slider. Direct manipulation replaces
   the remaining scheduled motion; **Reset** restores the preset from time
   zero.
4. Switch among Stable Fluids, D2Q9 LBM, and PIC/FLIP with the panel or keys
   `1`, `2`, and `3`. Their wakes should tell a compatible qualitative story,
   but they are deliberately different numerical models.
5. Pause with `Space`, then compare **Normal**, **Additive**, and **Screen**
   trail blending on exactly the same frame.
6. Toggle **Material tracers** with `T` and notice that persistent parcels can
   leave depleted regions where the ordinary display mode maintains even
   visual coverage.

## Presets

All four built-in presets use a NACA 2412 foil and continue running after the
last scheduled keyframe by holding its final pose.

| Preset | Setup | What to watch |
| --- | --- | --- |
| **Dynamic stall** | 160×96, Re = 1,000, 4° → 14° → 25° → 4°, Stable Fluids starts with MacCormack transport | The change from attached to separated flow, the growing wake, and the fact that returning the foil does not instantly restore the earlier field. |
| **Fixed stall** | 160×96, Re = 1,000, held at 25° | A sustained separated region and an unsteady wake without a changing control schedule. |
| **Chaotic wake** | 160×96, Re = 10,000, held at 35°, Stable Fluids starts with skew-RK2 transport and cropped edges | An energetic, irregular, multiscale two-dimensional wake. This is the optional deterministic-chaos experiment, not a claim of three-dimensional turbulence. |
| **High-resolution reference** | 384×192, Re = 500, the dynamic 4° → 14° → 25° → 4° schedule | A more finely sampled comparison that is substantially heavier on the CPU. “Reference” means higher spatial resolution here, not engineering-grade truth. |

Changing preset creates a fresh session: time, solver-private state, manual
pose, tuning, and tracer history reset to the selected scenario. **Import
scenario** accepts a local JSON file only after validating it against the
shared scenario schema. Imported files do not leave the browser.

## Numerical solvers

FoilBench exposes the same three solver families through both browser engines.

### Stable Fluids

Stable Fluids stores velocity on a staggered MAC grid, transports the field,
and projects it toward incompressibility. It is the clearest general-purpose
view of the flow and offers transport tuning:

- **MacCormack** is the robust, sharper default for ordinary play.
- **Skew RK2** retains more small-scale energy and enables the optional
  irregular-wake experiment, but it is less forgiving at extreme Reynolds
  numbers and angles.
- **Semi-Lagrangian** is the smoothest and most dissipative mode. A custom
  scenario can select it; the ordinary panel steps toward MacCormack or
  skew-RK2.

### D2Q9 TRT LBM

The lattice-Boltzmann solver evolves nine particle populations per cell and
uses a two-relaxation-time collision model. Its lattice Mach and relaxation
limits can force an **effective Reynolds number** below the requested value at
coarse resolution. That clamp is reported rather than hidden. LBM tuning is
automatic, so `[` and `]` do not expose another live parameter.

### Blended PIC/FLIP

PIC/FLIP transfers velocity between solver particles and the MAC grid, then
uses the grid pressure projection. It defaults to 95% FLIP and 5% PIC. More
FLIP retains energy and detail but can be noisier; more PIC damps motion and is
more forgiving. The `[` and `]` controls move the blend by five percentage
points.

The solver particles are not the cyan visible tracers. Solver particles are
private numerical state; visible tracers are passive presentation state shared
by every solver family.

## TypeScript and Rust/WASM engines

**TypeScript** is the default browser engine and independently implements all
three solvers with typed arrays. **Rust/WASM** runs the same Rust core used by
FoilBench's native Rust commands, compiled to WebAssembly. The TypeScript path
has been faster on the development machine, but the comparison depends on the
browser, CPU, and solver.

Switching solver family within one engine attempts a warm conversion at a
completed step boundary. It preserves physical time, Reynolds selection,
foil pose, applicable tuning, and visible tracer history. Because each solver
has different private state, the imported field can show a genuine conversion
transient; if conversion is unsafe, the viewer can make a classified fresh
fallback instead. That fallback preserves the authoritative physical controls
but discards incompatible solver-private history and reseeds the tracers.

Changing engine is intentionally a cold restart. It preserves the visible
foil pose, selected Reynolds number, and applicable tuning, but resets
physical time, solver-private history, and tracer paths. Cross-engine live
state conversion is not advertised.

## Simulation controls

- **Drag around the foil** or use the angle slider to select an angle between
  −30° and +30°. Positive displayed angle raises the foil's nose. Manual
  interaction cancels the remaining preset schedule until reset or a new
  preset is selected.
- **Pause/Resume** stops or resumes solver advancement without discarding the
  current field. **Reset** returns to the selected preset's time-zero state,
  schedule, Reynolds number, tuning, and tracers.
- The **Reynolds number** slider covers 50 to 100,000. `+` and `−` change it by
  a quarter decade (about 1.78×); `0` restores the preset value. The solver's
  viscosity or lattice scaling changes accordingly, while the viewer
  deliberately compresses the playback-rate change to remain watchable.
- `[` and `]` adjust the active solver's tuning as described above. The panel
  names the parameter and current value instead of exposing an internal ID.
- The header status bulb distinguishes warming, running, paused, and failed
  states. Short notices over the scene explain restarts, recoveries, and other
  non-routine transitions.

Violent dragging, extreme tuning, or a numerically difficult state can exceed
a solver's validity envelope. The viewer first attempts a bounded fresh
recovery at the authoritative visible pose. Repeated rapid-motion failures can
temporarily ignore angular velocity while still following the pose; repeated
failure at the safe baseline deliberately pauses rather than entering an
endless restart loop. A recovery is a numerical safety event, not a physical
feature of the wake, and it discards solver-private history and reseeds visible
tracers.

## View controls and tracers

- **Vorticity** (`V`) shows signed local rotation behind the trails. Its
  normalization is for readable visualization, not comparison of absolute
  color intensity between unrelated runs.
- **Material tracers** (`T`) changes tracer lifecycle, not the flow. Ordinary
  **display tracers** have finite lifetimes and periodically respawn throughout
  the domain to keep recirculating regions legible. Material tracers do not
  expire merely because of age; they persist until they leave the domain or
  require collision recovery, then re-enter at the inlet. Both modes use
  midpoint-RK2 advection through the current frozen velocity field.
- **Crop edges** (`C`) hides a scenario-defined rim where open-boundary
  artifacts are most conspicuous. It changes only the camera and is available
  when the scenario defines a crop margin.
- **Live diagnostics** (`D`) changes diagnostics and vorticity from their
  normal 0.1-second cadence to every accepted solver step. This can reduce
  throughput, especially for a heavy preset.

The SPA draws trail segments without point heads. Older segments fade, and
faster local motion appears brighter. The **Trail blending** selector changes
only how those already-generated segments combine in the framebuffer:

- **Normal** is restrained source-over compositing and remains the default.
- **Additive** accumulates light directly where trails overlap, emphasizing
  dense structures and sometimes saturating bright regions.
- **Screen** brightens overlaps more gently while retaining more tonal range.

All three modes use the same premultiplied-alpha shader, geometry, GPU buffer,
and rendering pass. Switching is immediate and does not advance or restart the
simulation. Browser color management and GPU composition can make their exact
appearance vary slightly across embedded and standalone browsers.

## Keyboard reference

| Key | Action |
| --- | --- |
| `1`, `2`, `3` | Select Stable Fluids, D2Q9 LBM, or PIC/FLIP |
| `Space` | Pause or resume |
| `R` | Reset the selected preset |
| `+`, `−`, `0` | Raise, lower, or reset Reynolds number |
| `[`, `]` | Adjust Stable Fluids transport or the PIC/FLIP blend |
| `V` | Toggle vorticity |
| `T` | Toggle display/material tracer lifecycle |
| `C` | Toggle the scenario's diagnostic crop |
| `D` | Toggle cadenced/every-step diagnostics |
| `Escape` | Close the controls drawer on a narrow screen |

Shortcuts are ignored while focus is in an interactive control. `Ctrl`,
`Command`, or `Alt` modified keystrokes are also ignored, so browser commands
such as copy do not accidentally change the simulation.

## Reading the displays

The stage reports angle of attack, requested Reynolds number, and **solver
steps per second**. The rate is solver throughput, not display frames per
second. The diagnostics card adds:

- kinetic energy and enstrophy as global activity measures;
- maximum divergence and wall leakage as numerical-health indicators;
- maximum speed; and
- **sim / wall**, the simulated seconds advanced per wall-clock second.

FoilBench does not catch up by hiding slow work. A demanding solver or preset
advances less simulated time per real second, and the displayed rates report
that honestly.

## Pedagogical limits

FoilBench is intended to make qualitative numerical-fluid concepts tangible,
not to predict aircraft performance.

- Every current solver is two-dimensional. The irregular skew-RK2 wake has
  two-dimensional cascade physics and no three-dimensional vortex stretching.
- The grids are coarse, the domain is finite, the open boundaries and moving
  foil are approximations, and no turbulence model turns the result into
  validated high-Reynolds-number engineering CFD.
- The three methods share contracts and test cases, not identical private
  physics. Pointwise disagreement and different numerical dissipation are
  expected; compare robust structures and trends rather than treating one
  colorful frame as ground truth.
- Tracers, trail brightness, blending, crop, and vorticity colors are
  visualization choices. They do not add mass, alter the velocity field, or
  directly represent force, pressure, density, or probability.
- Reynolds changes, solver switches, and forced recoveries create numerical
  transients. The interface reports them so they are not mistaken for physical
  events.

Within those limits, the lab is useful for seeing attached flow, separation,
vortex shedding, wake memory, numerical dissipation, particle/grid transfer,
and sensitivity to control history—and for comparing how several algorithms
construct those ideas.
