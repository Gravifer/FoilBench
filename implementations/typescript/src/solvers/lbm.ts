import type {CanonicalFlowState, ControlState, Diagnostics, FlowSolver, FloatArray, ImportOutcome, Scenario, SolverInfo, StepReport} from "../core/contracts.js";
import {NumericalFailure} from "../core/contracts.js";
import {NacaFoil} from "../core/geometry.js";
import {allocate, bounds2d, dimensions, sampleCell} from "../core/grid.js";
import {fieldDiagnostics} from "../core/metrics.js";
import {validateCanonicalState} from "../core/stateValidation.js";

const CX = [0, 1, 0, -1, 0, 1, -1, -1, 1] as const;
const CY = [0, 0, 1, 0, -1, 1, 1, -1, -1] as const;
const W = [4 / 9, 1 / 9, 1 / 9, 1 / 9, 1 / 9, 1 / 36, 1 / 36, 1 / 36, 1 / 36] as const;
const OPPOSITE = [0, 3, 4, 1, 2, 7, 8, 5, 6] as const;
const LATTICE_SOUND_SPEED = 1 / Math.sqrt(3);
const MAXIMUM_MACH = 0.08;
const MAXIMUM_LATTICE_SPEED = MAXIMUM_MACH * LATTICE_SOUND_SPEED;
const MAXIMUM_SUBSTEPS = 512;

export class LbmSolver implements FlowSolver {
  public readonly info: SolverInfo = {id: "lbm-d2q9", displayName: "D2Q9 TRT LBM", dimensions: [2], supportsMovingBoundary: true, acceleration: "typed-arrays"};
  public reynolds = 1;
  private effectiveReynolds = 1; private scenario: Scenario | null = null; private foil: NacaFoil | null = null;
  private populations: FloatArray = new Float32Array(); private scratch: FloatArray = new Float32Array(); private solid = new Uint8Array(); private time = 0; private control: ControlState = {time: 0, angleDegrees: 0, angularVelocityDegrees: 0};
  private latticeSpeed = 0.05; private omegaPlus = 1; private omegaMinus = 1; private referenceSpeed = 1;

  private requireScenario(): Scenario { if (this.scenario === null) throw new Error("solver is not initialized"); return this.scenario; }
  private equilibrium(direction: number, density: number, ux: number, uy: number): number { const cu = (CX[direction] ?? 0) * ux + (CY[direction] ?? 0) * uy; return (W[direction] ?? 0) * density * (1 + 3 * cu + 4.5 * cu * cu - 1.5 * (ux * ux + uy * uy)); }

  public initialize(scenario: Scenario, seed: number): void {
    void seed; if (scenario.domain.dimension !== 2) throw new RangeError("lbm-d2q9 supports only 2D"); this.scenario = scenario; this.foil = new NacaFoil(scenario.foil); this.reynolds = scenario.reynolds; this.time = 0; this.control = {time: 0, angleDegrees: scenario.controls[0]?.angleDegrees ?? 0, angularVelocityDegrees: 0};
    const {nx, ny} = dimensions(scenario.domain); const count = nx * ny; this.populations = new Float32Array(); this.scratch = new Float32Array();
    this.referenceSpeed = Math.max(Math.hypot(scenario.freestream[0] ?? 0, scenario.freestream[1] ?? 0), scenario.solverOptions.initialCondition === "freestream" ? 1e-6 : 1);
    this.configureTemporalScaling(scenario.outputDt); this.populations = allocate(scenario.precision, 9 * count); this.scratch = allocate(scenario.precision, 9 * count); this.solid = new Uint8Array(count); this.updateSolid(this.control);
    const velocity = this.initialVelocity();
    for (let cell = 0; cell < count; cell += 1) for (let q = 0; q < 9; q += 1) this.populations[q * count + cell] = this.equilibrium(q, 1, (velocity[2 * cell] ?? 0) * this.latticeSpeed / this.referenceSpeed, (velocity[2 * cell + 1] ?? 0) * this.latticeSpeed / this.referenceSpeed);
  }

  private configureRelaxation(): void { const scenario = this.requireScenario(); const {dx} = dimensions(scenario.domain); const chordCells = scenario.foil.chord / dx; const requestedNu = this.latticeSpeed * chordCells / this.reynolds; const minimumNu = (0.52 - 0.5) / 3; const nu = Math.max(requestedNu, minimumNu); const tauPlus = 0.5 + 3 * nu; const tauMinus = 0.5 + (3 / 16) / Math.max(tauPlus - 0.5, 1e-6); this.omegaPlus = 1 / tauPlus; this.omegaMinus = 1 / tauMinus; this.effectiveReynolds = this.latticeSpeed * chordCells / nu; }

  private configureTemporalScaling(targetDt: number, maximumPhysicalSpeed = this.referenceSpeed): number {
    const scenario = this.requireScenario(); const {dx} = dimensions(scenario.domain);
    const substeps = Math.max(1, Math.ceil(targetDt * Math.max(maximumPhysicalSpeed, this.referenceSpeed) / (MAXIMUM_LATTICE_SPEED * dx) - 1e-12));
    if (substeps > MAXIMUM_SUBSTEPS) throw new NumericalFailure("excessive_velocity", `LBM requires ${String(substeps)} substeps to respect its Mach limit`);
    const selectedSpeed = this.referenceSpeed * targetDt / (substeps * dx);
    if (this.populations.length > 0 && Math.abs(selectedSpeed - this.latticeSpeed) > 1e-12) this.rescalePopulations(selectedSpeed);
    this.latticeSpeed = selectedSpeed; this.configureRelaxation();
    return substeps;
  }

  private rescalePopulations(selectedSpeed: number): void {
    const scenario = this.requireScenario(); const {nx, ny} = dimensions(scenario.domain); const count = nx * ny;
    const {density, velocity} = this.latticeFields(); const ratio = selectedSpeed / this.latticeSpeed;
    const rescaled = allocate(scenario.precision, this.populations.length);
    for (let cell = 0; cell < count; cell += 1) {
      const rho = density[cell] ?? 1; const oldUx = velocity[2 * cell] ?? 0; const oldUy = velocity[2 * cell + 1] ?? 0; const newUx = oldUx * ratio; const newUy = oldUy * ratio;
      for (let q = 0; q < 9; q += 1) {
        const oldEquilibrium = this.equilibrium(q, rho, oldUx, oldUy); const newEquilibrium = this.equilibrium(q, rho, newUx, newUy);
        rescaled[q * count + cell] = newEquilibrium + ratio * ((this.populations[q * count + cell] ?? 0) - oldEquilibrium);
      }
    }
    this.populations = rescaled; this.scratch = allocate(scenario.precision, rescaled.length);
  }
  public setReynolds(reynolds: number): void { if (!(reynolds > 0) || !Number.isFinite(reynolds)) throw new RangeError("Reynolds must be finite and positive"); this.reynolds = reynolds; if (this.scenario !== null) this.configureRelaxation(); }

  private initialVelocity(): FloatArray { const scenario = this.requireScenario(); const {nx, ny, dx, dy} = dimensions(scenario.domain); const {x: bx, y: by} = bounds2d(scenario.domain); const output = allocate(scenario.precision, nx * ny * 2); for (let y = 0; y < ny; y += 1) for (let x = 0; x < nx; x += 1) { const cell = y * nx + x; const px = bx[0] + (x + 0.5) * dx; const py = by[0] + (y + 0.5) * dy; if (scenario.solverOptions.initialCondition === "taylor-green") { output[2 * cell] = Math.sin(px) * Math.cos(py); output[2 * cell + 1] = -Math.cos(px) * Math.sin(py); } else if (scenario.solverOptions.initialCondition === "poiseuille") { const center = (by[0] + by[1]) / 2; const radius = (by[1] - by[0]) / 2; output[2 * cell] = 1.5 * (1 - ((py - center) / radius) ** 2); } else { output[2 * cell] = scenario.freestream[0] ?? 0; output[2 * cell + 1] = scenario.freestream[1] ?? 0; } } return output; }
  private updateSolid(control: ControlState): void { const scenario = this.requireScenario(); const foil = this.foil; if (foil === null) throw new Error("foil is missing"); const {nx, ny, dx, dy} = dimensions(scenario.domain); const {x: bx, y: by} = bounds2d(scenario.domain); for (let y = 0; y < ny; y += 1) for (let x = 0; x < nx; x += 1) this.solid[y * nx + x] = foil.signedDistance(bx[0] + (x + 0.5) * dx, by[0] + (y + 0.5) * dy, control.angleDegrees) <= 0 ? 1 : 0; }

  private wallLatticeVelocity(x: number, y: number, control: ControlState): readonly [number, number] { const scenario = this.requireScenario(); const omega = control.angularVelocityDegrees * Math.PI / 180; const pivotX = scenario.foil.pivot[0] ?? 0; const pivotY = scenario.foil.pivot[1] ?? 0; const scale = this.latticeSpeed / this.referenceSpeed; return [-omega * (y - pivotY) * scale, omega * (x - pivotX) * scale]; }

  private applyOpenBoundaries(populations: FloatArray): void { const scenario = this.requireScenario(); const {nx, ny} = dimensions(scenario.domain); const count = nx * ny; const periodicX = scenario.domain.periodicAxes.includes("x"); const periodicY = scenario.domain.periodicAxes.includes("y"); const channelWalls = scenario.solverOptions.initialCondition === "poiseuille"; const ux = (scenario.freestream[0] ?? 0) * this.latticeSpeed / this.referenceSpeed; const uy = (scenario.freestream[1] ?? 0) * this.latticeSpeed / this.referenceSpeed; if (!periodicX) { for (let y = 0; y < ny; y += 1) for (let q = 0; q < 9; q += 1) { populations[q * count + y * nx] = this.equilibrium(q, 1, ux, uy); populations[q * count + y * nx + nx - 1] = populations[q * count + y * nx + Math.max(0, nx - 2)] ?? this.equilibrium(q, 1, ux, uy); } } if (!periodicY && !channelWalls) for (let x = 0; x < nx; x += 1) for (let q = 0; q < 9; q += 1) { populations[q * count + x] = this.equilibrium(q, 1, ux, uy); populations[q * count + (ny - 1) * nx + x] = this.equilibrium(q, 1, ux, uy); } const spongeStart = Math.max(1, Math.floor(0.88 * nx)); if (!periodicX) for (let x = spongeStart; x < nx; x += 1) { const fraction = ((x - spongeStart + 1) / Math.max(1, nx - spongeStart)) ** 2 * 0.18; for (let y = 1; y + 1 < ny; y += 1) { const cell = y * nx + x; if (this.solid[cell] !== 0) continue; for (let q = 0; q < 9; q += 1) { const index = q * count + cell; populations[index] = (1 - fraction) * (populations[index] ?? 0) + fraction * this.equilibrium(q, 1, ux, uy); } } } }

  private latticeFields(): {density: FloatArray; velocity: FloatArray} { const scenario = this.requireScenario(); const {nx, ny} = dimensions(scenario.domain); const count = nx * ny; const density = allocate(scenario.precision, count); const velocity = allocate(scenario.precision, 2 * count); for (let cell = 0; cell < count; cell += 1) { let rho = 0; let mx = 0; let my = 0; for (let q = 0; q < 9; q += 1) { const value = this.populations[q * count + cell] ?? 0; rho += value; mx += value * (CX[q] ?? 0); my += value * (CY[q] ?? 0); } density[cell] = rho; velocity[2 * cell] = mx / Math.max(rho, 1e-12); velocity[2 * cell + 1] = my / Math.max(rho, 1e-12); } return {density, velocity}; }

  private latticeStep(control: ControlState): void { const scenario = this.requireScenario(); const {nx, ny, dx, dy} = dimensions(scenario.domain); const {x: bx, y: by} = bounds2d(scenario.domain); const count = nx * ny; const previousSolid = this.solid.slice(); this.updateSolid(control); const inletUx = (scenario.freestream[0] ?? 0) * this.latticeSpeed / this.referenceSpeed; const inletUy = (scenario.freestream[1] ?? 0) * this.latticeSpeed / this.referenceSpeed; for (let cell = 0; cell < count; cell += 1) if (previousSolid[cell] !== 0 && this.solid[cell] === 0) for (let q = 0; q < 9; q += 1) this.populations[q * count + cell] = this.equilibrium(q, 1, inletUx, inletUy); const {density, velocity} = this.latticeFields(); const post = this.scratch; post.fill(0); for (let cell = 0; cell < count; cell += 1) { if (this.solid[cell] !== 0) continue; const rho = density[cell] ?? 1; const ux = velocity[2 * cell] ?? 0; const uy = velocity[2 * cell + 1] ?? 0; for (let q = 0; q < 9; q += 1) { const opposite = OPPOSITE[q] ?? 0; const f = this.populations[q * count + cell] ?? 0; const fo = this.populations[opposite * count + cell] ?? 0; const eq = this.equilibrium(q, rho, ux, uy); const eqo = this.equilibrium(opposite, rho, ux, uy); const symmetric = 0.5 * (f + fo); const antisymmetric = 0.5 * (f - fo); post[q * count + cell] = f - this.omegaPlus * (symmetric - 0.5 * (eq + eqo)) - this.omegaMinus * (antisymmetric - 0.5 * (eq - eqo)); } }
    const next = allocate(scenario.precision, this.populations.length); for (let cell = 0; cell < count; cell += 1) for (let q = 0; q < 9; q += 1) next[q * count + cell] = this.equilibrium(q, 1, inletUx, inletUy);
    const periodicX = scenario.domain.periodicAxes.includes("x"); const periodicY = scenario.domain.periodicAxes.includes("y"); const channelWalls = scenario.solverOptions.initialCondition === "poiseuille";
    const foil = this.foil; if (foil === null) throw new Error("foil is missing"); for (let y = 0; y < ny; y += 1) for (let x = 0; x < nx; x += 1) { const cell = y * nx + x; if (this.solid[cell] !== 0) continue; for (let q = 0; q < 9; q += 1) { let tx = x + (CX[q] ?? 0); let ty = y + (CY[q] ?? 0); const value = post[q * count + cell] ?? 0; if (periodicX) tx = (tx + nx) % nx; if (periodicY) ty = (ty + ny) % ny; if (channelWalls && (ty < 0 || ty >= ny)) { next[(OPPOSITE[q] ?? 0) * count + cell] = value; continue; } if (tx < 0 || tx >= nx || ty < 0 || ty >= ny) continue; const target = ty * nx + tx; if (this.solid[target] !== 0) { const px = bx[0] + (x + 0.5) * dx; const py = by[0] + (y + 0.5) * dy; const targetX = bx[0] + (tx + 0.5) * dx; const targetY = by[0] + (ty + 0.5) * dy; const sourceDistance = Math.max(foil.signedDistance(px, py, control.angleDegrees), 1e-8); const targetDistance = foil.signedDistance(targetX, targetY, control.angleDegrees); const fraction = Math.max(0.05, Math.min(0.95, sourceDistance / Math.max(sourceDistance - targetDistance, 1e-8))); const upstreamX = x - (CX[q] ?? 0); const upstreamY = y - (CY[q] ?? 0); const opposite = OPPOSITE[q] ?? 0; let reflected: number; if (fraction < 0.5 && upstreamX >= 0 && upstreamX < nx && upstreamY >= 0 && upstreamY < ny) reflected = 2 * fraction * value + (1 - 2 * fraction) * (post[q * count + upstreamY * nx + upstreamX] ?? value); else reflected = value / (2 * fraction) + (2 * fraction - 1) / (2 * fraction) * (post[opposite * count + cell] ?? value); const wall = this.wallLatticeVelocity(px + fraction * (targetX - px), py + fraction * (targetY - py), control); reflected -= 6 * (W[q] ?? 0) * (density[cell] ?? 1) * ((CX[q] ?? 0) * wall[0] + (CY[q] ?? 0) * wall[1]); next[opposite * count + cell] = reflected; } else next[q * count + target] = value; } }
    this.applyOpenBoundaries(next);
    this.populations = next; this.scratch = post;
  }

  public advance(control: ControlState, targetDt: number): StepReport {
    if (!(targetDt > 0) || !Number.isFinite(targetDt)) throw new RangeError("target dt must be finite and positive");
    const saved = this.populations.slice(); const savedScratch = this.scratch.slice(); const savedSolid = this.solid.slice(); const savedSpeed = this.latticeSpeed; const savedOmegaPlus = this.omegaPlus; const savedOmegaMinus = this.omegaMinus; const savedEffectiveReynolds = this.effectiveReynolds;
    let substeps = 0; let maxSpeed = 0;
    try {
      const current = this.physicalVelocity(); let maximumPhysicalSpeed = this.referenceSpeed; for (let index = 0; index < current.length; index += 2) maximumPhysicalSpeed = Math.max(maximumPhysicalSpeed, Math.hypot(current[index] ?? 0, current[index + 1] ?? 0));
      substeps = this.configureTemporalScaling(targetDt, maximumPhysicalSpeed);
      for (let step = 0; step < substeps; step += 1) {
        const fraction = (step + 1) / substeps;
        const subControl: ControlState = {time: this.time + fraction * targetDt, angleDegrees: this.control.angleDegrees + fraction * (control.angleDegrees - this.control.angleDegrees), angularVelocityDegrees: control.angularVelocityDegrees};
        this.latticeStep(subControl);
      }
      if (!this.populations.every(Number.isFinite)) throw new NumericalFailure("nonfinite_state", "LBM populations became non-finite");
      if (!this.latticeFields().density.every((value) => Number.isFinite(value) && value > 0)) throw new NumericalFailure("invalid_density", "LBM density became non-positive or non-finite");
      const physical = this.physicalVelocity(); for (let i = 0; i < physical.length; i += 2) maxSpeed = Math.max(maxSpeed, Math.hypot(physical[i] ?? 0, physical[i + 1] ?? 0));
      if (!Number.isFinite(maxSpeed)) throw new NumericalFailure("nonfinite_state", "LBM produced a non-finite step report");
    } catch (error) {
      this.populations = saved; this.scratch = savedScratch; this.solid = savedSolid; this.latticeSpeed = savedSpeed; this.omegaPlus = savedOmegaPlus; this.omegaMinus = savedOmegaMinus; this.effectiveReynolds = savedEffectiveReynolds; throw error;
    }
    this.time += targetDt; this.control = {...control, time: this.time};
    return {requestedDt: targetDt, advancedDt: targetDt, substeps, maxSpeed, warnings: this.effectiveReynolds < this.reynolds ? ["effective Reynolds clamped to " + String(this.effectiveReynolds)] : []};
  }
  private physicalVelocity(): FloatArray { const scenario = this.requireScenario(); const lattice = this.latticeFields().velocity; const output = allocate(scenario.precision, lattice.length); const scale = this.referenceSpeed / this.latticeSpeed; for (let cell = 0; cell < this.solid.length; cell += 1) { if (this.solid[cell] !== 0) continue; output[2 * cell] = (lattice[2 * cell] ?? 0) * scale; output[2 * cell + 1] = (lattice[2 * cell + 1] ?? 0) * scale; } return output; }
  public sampleVelocity(points: FloatArray): FloatArray { const scenario = this.requireScenario(); const velocity = this.physicalVelocity(); const output = allocate(scenario.precision, points.length); for (let i = 0; i < points.length; i += 2) { const value = sampleCell(velocity, scenario.domain, points[i] ?? 0, points[i + 1] ?? 0); output[i] = value[0]; output[i + 1] = value[1]; } return output; }
  public exportState(): CanonicalFlowState { const scenario = this.requireScenario(); return {schemaVersion: 1, dimension: 2, bounds: scenario.domain.bounds, resolution: scenario.domain.resolution, periodicAxes: scenario.domain.periodicAxes, time: this.time, precision: scenario.precision, angleDegrees: this.control.angleDegrees, angularVelocityDegrees: this.control.angularVelocityDegrees, sourceLanguage: "typescript", sourceSolver: this.info.id, velocity: this.physicalVelocity(), density: this.latticeFields().density}; }
  public importState(state: CanonicalFlowState, control: ControlState): ImportOutcome {
    const scenario = this.requireScenario(); const invalid = validateCanonicalState(state, scenario); if (invalid !== null) return invalid;
    const {nx, ny} = dimensions(scenario.domain);
    if (state.density !== null && state.density.some((value) => value <= 0)) return {status: "rejected", reason: "invalid_density", discardedState: [], warnings: []};
    let maximum = 0; for (let cell = 0; cell < nx * ny; cell += 1) maximum = Math.max(maximum, Math.hypot(state.velocity[2 * cell] ?? 0, state.velocity[2 * cell + 1] ?? 0));
    const saved = this.populations; const savedScratch = this.scratch; const savedSolid = this.solid; const savedTime = this.time; const savedControl = this.control; const savedSpeed = this.latticeSpeed; const savedOmegaPlus = this.omegaPlus; const savedOmegaMinus = this.omegaMinus; const savedEffectiveReynolds = this.effectiveReynolds;
    try {
      this.configureTemporalScaling(scenario.outputDt, maximum);
      if (maximum * this.latticeSpeed / (this.referenceSpeed * LATTICE_SOUND_SPEED) > MAXIMUM_MACH + 1e-12) throw new NumericalFailure("excessive_velocity", "LBM import exceeds its Mach limit");
      const count = nx * ny; const density = state.density; this.populations = allocate(scenario.precision, 9 * count);
      for (let cell = 0; cell < count; cell += 1) for (let q = 0; q < 9; q += 1) this.populations[q * count + cell] = this.equilibrium(q, density?.[cell] ?? 1, (state.velocity[2 * cell] ?? 0) * this.latticeSpeed / this.referenceSpeed, (state.velocity[2 * cell + 1] ?? 0) * this.latticeSpeed / this.referenceSpeed);
      this.solid = new Uint8Array(count); this.time = state.time; this.control = control; this.updateSolid(control);
    } catch (error) {
      this.populations = saved; this.scratch = savedScratch; this.solid = savedSolid; this.time = savedTime; this.control = savedControl; this.latticeSpeed = savedSpeed; this.omegaPlus = savedOmegaPlus; this.omegaMinus = savedOmegaMinus; this.effectiveReynolds = savedEffectiveReynolds;
      if (error instanceof NumericalFailure) return {status: "rejected", reason: error.reason, discardedState: [], warnings: []};
      throw error;
    }
    return {status: "accepted", reason: "none", discardedState: ["non-equilibrium populations"], warnings: []};
  }
  public diagnostics(): Diagnostics { const scenario = this.requireScenario(); const foil = this.foil; if (foil === null) throw new Error("foil is missing"); const fields = this.latticeFields(); const velocity = this.physicalVelocity(); let densityDrift = 0; for (let cell = 0; cell < fields.density.length; cell += 1) densityDrift = Math.max(densityDrift, Math.abs((fields.density[cell] ?? 1) - 1)); return {values: {...fieldDiagnostics(velocity, scenario, foil, this.control.angleDegrees), density_drift_linf: densityDrift, effective_reynolds: this.effectiveReynolds}, warnings: []}; }
}
