import type {CanonicalFlowState, ControlState, Diagnostics, FailureEvidence, FlowSolver, FloatArray, ImportOutcome, InteractiveTuning, InteractiveTuningValue, RestartState, ReynoldsOutcome, Scenario, SolverInfo, StepReport} from "../core/contracts.js";
import {NumericalFailure} from "../core/contracts.js";
import {NacaFoil} from "../core/geometry.js";
import {allocate, bounds2d, dimensions} from "../core/grid.js";
import {Pcg32} from "../core/rng.js";
import {acceptedImport} from "../core/outcomes.js";
import {StableFluidsSolver} from "./stableFluids.js";
import type {StableCheckpoint} from "./stableFluids.js";

interface ParticleCheckpoint {
  readonly grid: StableCheckpoint;
  readonly x: FloatArray; readonly y: FloatArray; readonly vx: FloatArray; readonly vy: FloatArray;
  readonly generations: Uint32Array; readonly rng: bigint; readonly advanceCount: number; readonly settlingSteps: number;
  readonly stateRevision: number;
  readonly unsupportedFaceFraction: number;
}

export class PicFlipSolver implements FlowSolver {
  public readonly info: SolverInfo = {id: "pic-flip", displayName: "Blended PIC/FLIP", dimensions: [2], supportsMovingBoundary: true, supportedPrecisions: ["float32", "float64"], acceleration: "typed-arrays"};
  public get reynolds(): number { return this.grid.reynolds; }
  private readonly grid = new StableFluidsSolver(); private scenario: Scenario | null = null; private foil: NacaFoil | null = null;
  private x: FloatArray = new Float32Array(); private y: FloatArray = new Float32Array(); private vx: FloatArray = new Float32Array(); private vy: FloatArray = new Float32Array(); private generations: Uint32Array = new Uint32Array();
  private rng = new Pcg32(0, 71); private blend = 0.95; private cfl = 0.75; private advanceCount = 0; private settlingSteps = 0;
  private revision = 0;
  private unsupportedFaceFraction = 0;
  public get stateRevision(): number { return this.revision; }
  private rollback: ParticleCheckpoint | null = null; private oldFaces: StableCheckpoint | null = null; private newFaces: StableCheckpoint | null = null; private populationFaces: StableCheckpoint | null = null;

  private requireScenario(): Scenario { if (this.scenario === null) throw new Error("solver is not initialized"); return this.scenario; }
  private requireFoil(): NacaFoil { if (this.foil === null) throw new Error("foil is missing"); return this.foil; }

  public initialize(scenario: Scenario, seed: number): void {
    this.restart(scenario, seed, {time: 0, angleDegrees: scenario.controls[0]?.angleDegrees ?? 0, reynolds: scenario.reynolds});
  }

  public restart(scenario: Scenario, seed: number, start: RestartState): void {
    if (scenario.domain.dimension !== 2) throw new RangeError("pic-flip supports only 2D");
    if (!Number.isFinite(start.time) || start.time < 0 || !Number.isFinite(start.angleDegrees) || !Number.isFinite(start.reynolds) || start.reynolds <= 0) throw new RangeError("invalid PIC/FLIP restart state");
    const configuredCfl = scenario.solverOptions.picCfl ?? 0.75; if (!(configuredCfl > 0 && configuredCfl <= 1) || !Number.isFinite(configuredCfl)) throw new RangeError("pic_cfl must be in (0, 1]");
    this.scenario = scenario; this.foil = new NacaFoil(scenario.foil); this.blend = scenario.solverOptions.picFlipBlend ?? 0.95; this.cfl = configuredCfl; this.rng = new Pcg32(seed, 71); this.advanceCount = 0; this.settlingSteps = 0; this.unsupportedFaceFraction = 0; this.rollback = null; this.oldFaces = null; this.newFaces = null; this.populationFaces = null;
    this.grid.restart(scenario, seed, start); this.seedParticles(start.angleDegrees);
    this.revision = 0;
  }

  public setReynolds(reynolds: number): ReynoldsOutcome { const previous = this.grid.reynolds; const outcome = this.grid.setReynolds(reynolds); if (previous !== this.grid.reynolds) this.revision += 1; return outcome; }
  public setBlend(blend: number): void { if (!Number.isFinite(blend)) throw new RangeError("PIC/FLIP blend must be finite"); const selected = Math.max(0, Math.min(1, blend)); if (selected !== this.blend) { this.blend = selected; this.revision += 1; } }
  public get currentBlend(): number { return this.blend; }
  public interactiveTuning(): InteractiveTuning { return {id: "pic-flip-blend", label: "FLIP", value: this.blend, canDecrease: this.blend > 0, canIncrease: this.blend < 1}; }
  public adjustInteractiveTuning(direction: -1 | 1): InteractiveTuning { this.setBlend(this.blend + 0.05 * direction); return this.interactiveTuning(); }
  public applyInteractiveTuning(value: InteractiveTuningValue): InteractiveTuning {
    if (typeof value !== "number") throw new RangeError("PIC/FLIP tuning must be numeric");
    this.setBlend(value);
    return this.interactiveTuning();
  }

  private assignParticles(xs: readonly number[], ys: readonly number[]): void {
    const scenario = this.requireScenario(); this.x = allocate(scenario.precision, xs.length); this.y = allocate(scenario.precision, ys.length); this.vx = allocate(scenario.precision, xs.length); this.vy = allocate(scenario.precision, ys.length); this.generations = new Uint32Array(xs.length);
    for (let index = 0; index < xs.length; index += 1) { this.x[index] = xs[index] ?? 0; this.y[index] = ys[index] ?? 0; }
    const faces = this.grid.checkpoint(); for (let index = 0; index < xs.length; index += 1) { const sampled = this.sampleFaces(faces, this.x[index] ?? 0, this.y[index] ?? 0); this.vx[index] = sampled[0]; this.vy[index] = sampled[1]; }
  }

  private seedParticles(angleDegrees: number): void {
    const scenario = this.requireScenario(); const foil = this.requireFoil(); const {nx, ny, dx, dy} = dimensions(scenario.domain); const {x: bx, y: by} = bounds2d(scenario.domain); const xs: number[] = []; const ys: number[] = [];
    for (let cy = 0; cy < ny; cy += 1) for (let cx = 0; cx < nx; cx += 1) {
      const centerX = bx[0] + (cx + 0.5) * dx; const centerY = by[0] + (cy + 0.5) * dy; if (foil.signedDistance(centerX, centerY, angleDegrees) <= 0) continue;
      for (let local = 0; local < 4; local += 1) for (let attempt = 0; attempt < 16; attempt += 1) {
        const px = bx[0] + (cx + 0.1 + 0.8 * this.rng.nextFloat32()) * dx; const py = by[0] + (cy + 0.1 + 0.8 * this.rng.nextFloat32()) * dy;
        if (foil.signedDistance(px, py, angleDegrees) > 0) { xs.push(px); ys.push(py); break; }
      }
    }
    this.assignParticles(xs, ys);
  }

  private quadraticWeight(distance: number): number { const value = Math.abs(distance); if (value < 0.5) return 0.75 - value * value; if (value < 1.5) return 0.5 * (1.5 - value) ** 2; return 0; }
  private wrapped(index: number, size: number): number { return ((index % size) + size) % size; }

  private sampleComponent(field: FloatArray, width: number, height: number, gx: number, gy: number, periodicX: boolean, periodicY: boolean, duplicateX: boolean, duplicateY: boolean): number {
    const uniqueWidth = duplicateX ? width - 1 : width; const uniqueHeight = duplicateY ? height - 1 : height; const baseX = Math.floor(gx - 0.5); const baseY = Math.floor(gy - 0.5); let value = 0; let total = 0;
    for (let oy = 0; oy < 3; oy += 1) for (let ox = 0; ox < 3; ox += 1) {
      let ix = baseX + ox; let iy = baseY + oy; if (periodicX) ix = this.wrapped(ix, uniqueWidth); if (periodicY) iy = this.wrapped(iy, uniqueHeight); if (ix < 0 || ix >= width || iy < 0 || iy >= height) continue;
      const weight = this.quadraticWeight(gx - (baseX + ox)) * this.quadraticWeight(gy - (baseY + oy)); value += weight * (field[iy * width + ix] ?? 0); total += weight;
    }
    return total > 1e-12 ? value / total : 0;
  }

  private sampleFaces(faces: StableCheckpoint, px: number, py: number): readonly [number, number] {
    const scenario = this.requireScenario(); const {nx, ny, dx, dy} = dimensions(scenario.domain); const {x: bx, y: by} = bounds2d(scenario.domain); const periodicX = scenario.domain.periodicAxes.includes("x"); const periodicY = scenario.domain.periodicAxes.includes("y");
    const u = this.sampleComponent(faces.u, nx + 1, ny, (px - bx[0]) / dx, (py - by[0]) / dy - 0.5, periodicX, periodicY, periodicX, false);
    const v = this.sampleComponent(faces.v, nx, ny + 1, (px - bx[0]) / dx - 0.5, (py - by[0]) / dy, periodicX, periodicY, false, periodicY);
    return [u, v];
  }

  private scatterComponent(values: FloatArray, positionsX: FloatArray, positionsY: FloatArray, width: number, height: number, gxOffset: number, gyOffset: number, periodicX: boolean, periodicY: boolean, duplicateX: boolean, duplicateY: boolean, fallback: FloatArray): {readonly field: FloatArray; readonly unsupported: number} {
    const scenario = this.requireScenario(); const {dx, dy} = dimensions(scenario.domain); const {x: bx, y: by} = bounds2d(scenario.domain); const output = allocate(scenario.precision, width * height); const weights = allocate(scenario.precision, width * height); const uniqueWidth = duplicateX ? width - 1 : width; const uniqueHeight = duplicateY ? height - 1 : height;
    for (let particle = 0; particle < positionsX.length; particle += 1) {
      const gx = ((positionsX[particle] ?? 0) - bx[0]) / dx + gxOffset; const gy = ((positionsY[particle] ?? 0) - by[0]) / dy + gyOffset; const baseX = Math.floor(gx - 0.5); const baseY = Math.floor(gy - 0.5);
      for (let oy = 0; oy < 3; oy += 1) for (let ox = 0; ox < 3; ox += 1) { let ix = baseX + ox; let iy = baseY + oy; if (periodicX) ix = this.wrapped(ix, uniqueWidth); if (periodicY) iy = this.wrapped(iy, uniqueHeight); if (ix < 0 || ix >= width || iy < 0 || iy >= height) continue; const weight = this.quadraticWeight(gx - (baseX + ox)) * this.quadraticWeight(gy - (baseY + oy)); const index = iy * width + ix; output[index] = (output[index] ?? 0) + weight * (values[particle] ?? 0); weights[index] = (weights[index] ?? 0) + weight; }
    }
    let unsupported = 0; for (let index = 0; index < output.length; index += 1) { if ((weights[index] ?? 0) <= 1e-12) unsupported += 1; output[index] = (weights[index] ?? 0) > 1e-12 ? (output[index] ?? 0) / (weights[index] ?? 1) : fallback[index] ?? 0; }
    if (duplicateX) for (let iy = 0; iy < height; iy += 1) output[iy * width + width - 1] = output[iy * width] ?? 0;
    if (duplicateY) for (let ix = 0; ix < width; ix += 1) output[(height - 1) * width + ix] = output[ix] ?? 0;
    return {field: output, unsupported};
  }

  private scatterToFaces(fallback: StableCheckpoint): {readonly u: FloatArray; readonly v: FloatArray} {
    const scenario = this.requireScenario(); const {nx, ny} = dimensions(scenario.domain); const periodicX = scenario.domain.periodicAxes.includes("x"); const periodicY = scenario.domain.periodicAxes.includes("y");
    const u = this.scatterComponent(this.vx, this.x, this.y, nx + 1, ny, 0, -0.5, periodicX, periodicY, periodicX, false, fallback.u); const v = this.scatterComponent(this.vy, this.x, this.y, nx, ny + 1, -0.5, 0, periodicX, periodicY, false, periodicY, fallback.v); this.unsupportedFaceFraction = (u.unsupported + v.unsupported) / (u.field.length + v.field.length); return {u: u.field, v: v.field};
  }

  private respawnAtInlet(index: number, angleDegrees: number, faces: StableCheckpoint): void {
    const scenario = this.requireScenario(); const foil = this.requireFoil(); const {dx} = dimensions(scenario.domain); const {x: bx, y: by} = bounds2d(scenario.domain); const periodicX = scenario.domain.periodicAxes.includes("x");
    for (let attempt = 0; attempt < 32; attempt += 1) { const px = periodicX ? bx[0] + this.rng.nextFloat32() * (bx[1] - bx[0]) : bx[0] + (0.1 + 0.8 * this.rng.nextFloat32()) * dx; const py = by[0] + this.rng.nextFloat32() * (by[1] - by[0]); if (foil.signedDistance(px, py, angleDegrees) > 0) { this.x[index] = px; this.y[index] = py; const sampled = this.sampleFaces(faces, px, py); this.vx[index] = sampled[0]; this.vy[index] = sampled[1]; this.generations[index] = (this.generations[index] ?? 0) + 1; return; } }
    throw new NumericalFailure("transfer_failure", "PIC/FLIP could not respawn a solver particle", "population-maintenance", {particle_index: index, angle_degrees: angleDegrees});
  }

  private applyParticleBoundary(index: number, oldX: number, oldY: number, control: ControlState, faces: StableCheckpoint): void {
    const scenario = this.requireScenario(); const foil = this.requireFoil(); const {dx, dy} = dimensions(scenario.domain); const {x: bx, y: by} = bounds2d(scenario.domain); const periodicX = scenario.domain.periodicAxes.includes("x"); const periodicY = scenario.domain.periodicAxes.includes("y"); let px = this.x[index] ?? 0; let py = this.y[index] ?? 0;
    if (periodicX) px = bx[0] + (((px - bx[0]) % (bx[1] - bx[0])) + (bx[1] - bx[0])) % (bx[1] - bx[0]);
    if (periodicY) py = by[0] + (((py - by[0]) % (by[1] - by[0])) + (by[1] - by[0])) % (by[1] - by[0]);
    this.x[index] = px; this.y[index] = py;
    if ((!periodicX && (px < bx[0] || px >= bx[1])) || (!periodicY && (py < by[0] || py >= by[1]))) { this.respawnAtInlet(index, control.angleDegrees, faces); return; }
    const middleDistance = foil.signedDistance(0.5 * (oldX + px), 0.5 * (oldY + py), control.angleDegrees); const distance = foil.signedDistance(px, py, control.angleDegrees); if (middleDistance > 0 && distance > 0) return; if (distance > 0) { this.respawnAtInlet(index, control.angleDegrees, faces); return; }
    const normal = foil.normal(px, py, control.angleDegrees); const norm = Math.hypot(normal[0], normal[1]); if (distance >= -1.5 * Math.min(dx, dy) && normal.every(Number.isFinite) && norm > 0.5) { const nx = normal[0] / norm; const ny = normal[1] / norm; const correction = -distance + 1e-3 * Math.min(dx, dy); this.x[index] = px + correction * nx; this.y[index] = py + correction * ny; const omega = control.angularVelocityDegrees * Math.PI / 180; const pivotX = scenario.foil.pivot[0] ?? 0; const pivotY = scenario.foil.pivot[1] ?? 0; const wallX = -omega * (py - pivotY); const wallY = omega * (px - pivotX); const relativeNormal = ((this.vx[index] ?? 0) - wallX) * nx + ((this.vy[index] ?? 0) - wallY) * ny; if (relativeNormal < 0) { this.vx[index] = (this.vx[index] ?? 0) - relativeNormal * nx; this.vy[index] = (this.vy[index] ?? 0) - relativeNormal * ny; } }
    else this.respawnAtInlet(index, control.angleDegrees, faces);
  }

  private maintainPopulation(control: ControlState, faces: StableCheckpoint): void {
    const scenario = this.requireScenario(); const foil = this.requireFoil(); const {nx, ny, dx, dy} = dimensions(scenario.domain); const {x: bx, y: by} = bounds2d(scenario.domain); const counts = new Int32Array(nx * ny); const cells = new Int32Array(this.x.length); cells.fill(-1);
    for (let index = 0; index < this.x.length; index += 1) { const cx = Math.floor(((this.x[index] ?? bx[0]) - bx[0]) / dx); const cy = Math.floor(((this.y[index] ?? by[0]) - by[0]) / dy); if (cx >= 0 && cx < nx && cy >= 0 && cy < ny && foil.signedDistance(this.x[index] ?? 0, this.y[index] ?? 0, control.angleDegrees) > 0) { const cell = cy * nx + cx; cells[index] = cell; counts[cell] = (counts[cell] ?? 0) + 1; } }
    const targets: number[] = []; for (let cy = 0; cy < ny; cy += 1) for (let cx = 0; cx < nx; cx += 1) { const cell = cy * nx + cx; if (foil.signedDistance(bx[0] + (cx + 0.5) * dx, by[0] + (cy + 0.5) * dy, control.angleDegrees) <= 0) continue; for (let missing = counts[cell] ?? 0; missing < 4; missing += 1) targets.push(cell); }
    const donors: number[] = []; const retained = counts.slice(); for (let index = 0; index < cells.length; index += 1) { const cell = cells[index] ?? -1; if (cell < 0 || (retained[cell] ?? 0) > 4) { donors.push(index); if (cell >= 0) retained[cell] = (retained[cell] ?? 0) - 1; } }
    const moves = Math.min(targets.length, donors.length); for (let move = 0; move < moves; move += 1) { const index = donors[move] ?? 0; const cell = targets[move] ?? 0; const cx = cell % nx; const cy = Math.floor(cell / nx); for (let attempt = 0; attempt < 16; attempt += 1) { const px = bx[0] + (cx + 0.1 + 0.8 * this.rng.nextFloat32()) * dx; const py = by[0] + (cy + 0.1 + 0.8 * this.rng.nextFloat32()) * dy; if (foil.signedDistance(px, py, control.angleDegrees) > 0) { this.x[index] = px; this.y[index] = py; const sampled = this.sampleFaces(faces, px, py); this.vx[index] = sampled[0]; this.vy[index] = sampled[1]; this.generations[index] = (this.generations[index] ?? 0) + 1; break; } } }
  }

  private populationEvidence(angleDegrees: number): Readonly<Record<string, number>> {
    const scenario = this.requireScenario(); const foil = this.requireFoil(); const {nx, ny, dx, dy} = dimensions(scenario.domain); const {x: bx, y: by} = bounds2d(scenario.domain); if (this.x.length !== this.y.length || this.x.length !== this.vx.length || this.x.length !== this.vy.length || this.x.length !== this.generations.length) throw new NumericalFailure("transfer_failure", "PIC/FLIP particle arrays disagree in length", "postcondition"); const counts = new Uint32Array(nx * ny); let unresolvedSolid = 0;
    for (let index = 0; index < this.x.length; index += 1) { const px = this.x[index] ?? Number.NaN; const py = this.y[index] ?? Number.NaN; const ux = this.vx[index] ?? Number.NaN; const uy = this.vy[index] ?? Number.NaN; if (![px, py, ux, uy].every(Number.isFinite)) throw new NumericalFailure("nonfinite_state", "PIC/FLIP particle state became non-finite", "postcondition", {particle_index: index}); if (foil.signedDistance(px, py, angleDegrees) <= 0) { unresolvedSolid += 1; continue; } const cx = Math.floor((px - bx[0]) / dx); const cy = Math.floor((py - by[0]) / dy); if (cx >= 0 && cx < nx && cy >= 0 && cy < ny) counts[cy * nx + cx] = (counts[cy * nx + cx] ?? 0) + 1; }
    let fluidCells = 0; let emptyCells = 0; let underfilledCells = 0; let maximumCellPopulation = 0; for (let cy = 0; cy < ny; cy += 1) for (let cx = 0; cx < nx; cx += 1) { if (foil.signedDistance(bx[0] + (cx + 0.5) * dx, by[0] + (cy + 0.5) * dy, angleDegrees) <= 0) continue; fluidCells += 1; const count = counts[cy * nx + cx] ?? 0; if (count === 0) emptyCells += 1; if (count < 4) underfilledCells += 1; maximumCellPopulation = Math.max(maximumCellPopulation, count); }
    if (unresolvedSolid > 0) throw new NumericalFailure("postcondition_failure", "PIC/FLIP left particles inside the solid", "postcondition", {unresolved_solid_particles: unresolvedSolid}); return {empty_cell_fraction: emptyCells / Math.max(1, fluidCells), underfilled_cell_fraction: underfilledCells / Math.max(1, fluidCells), maximum_cell_population: maximumCellPopulation, unresolved_solid_particles: unresolvedSolid};
  }

  private checkpoint(destination?: ParticleCheckpoint): ParticleCheckpoint {
    const copy = (source: FloatArray, selected?: FloatArray): FloatArray => { const output = selected?.length === source.length ? selected : source.slice(); if (selected !== undefined) output.set(source); return output; };
    const generations = destination?.generations.length === this.generations.length ? destination.generations : this.generations.slice();
    if (destination !== undefined) generations.set(this.generations);
    return {grid: this.grid.checkpoint(destination?.grid), x: copy(this.x, destination?.x), y: copy(this.y, destination?.y), vx: copy(this.vx, destination?.vx), vy: copy(this.vy, destination?.vy), generations, rng: this.rng.checkpoint(), advanceCount: this.advanceCount, settlingSteps: this.settlingSteps, stateRevision: this.revision, unsupportedFaceFraction: this.unsupportedFaceFraction};
  }
  private transactionCheckpoint(): ParticleCheckpoint { const saved = this.checkpoint(this.rollback ?? undefined); this.rollback = saved; return saved; }
  private restore(checkpoint: ParticleCheckpoint): void {
    this.grid.restore(checkpoint.grid);
    const restore = (current: FloatArray, saved: FloatArray): FloatArray => { if (current.length === saved.length) { current.set(saved); return current; } return saved.slice(); };
    this.x = restore(this.x, checkpoint.x); this.y = restore(this.y, checkpoint.y); this.vx = restore(this.vx, checkpoint.vx); this.vy = restore(this.vy, checkpoint.vy);
    if (this.generations.length === checkpoint.generations.length) this.generations.set(checkpoint.generations); else this.generations = checkpoint.generations.slice();
    this.rng.restore(checkpoint.rng); this.advanceCount = checkpoint.advanceCount; this.settlingSteps = checkpoint.settlingSteps; this.revision = checkpoint.stateRevision; this.unsupportedFaceFraction = checkpoint.unsupportedFaceFraction;
  }

  public advance(control: ControlState, targetDt: number): StepReport {
    if (!(targetDt > 0) || !Number.isFinite(targetDt)) throw new RangeError("target dt must be finite and positive"); const scenario = this.requireScenario(); const expectedTime = this.grid.exportState().time + targetDt; const timeTolerance = scenario.precision === "float32" ? 1e-6 : 1e-12; if (!Number.isFinite(control.time) || Math.abs(control.time - expectedTime) > timeTolerance * Math.max(1, Math.abs(expectedTime))) throw new NumericalFailure("time_contract_failure", "control completion time disagrees with target interval", "time-mapping", {expected_time: expectedTime, control_time: control.time, target_dt: targetDt}); const saved = this.transactionCheckpoint(); const {dx, dy} = dimensions(scenario.domain); let maximum = 0; for (let index = 0; index < this.x.length; index += 1) maximum = Math.max(maximum, Math.hypot(this.vx[index] ?? 0, this.vy[index] ?? 0)); const wallSpeed = Math.abs(control.angularVelocityDegrees) * Math.PI / 180 * scenario.foil.chord; const sweepSpeed = scenario.foil.chord * Math.abs(control.angleDegrees - saved.grid.control.angleDegrees) * Math.PI / (180 * targetDt); const resolvedSpeed = Math.max(maximum, wallSpeed, sweepSpeed, 1e-6); const substeps = Math.max(1, Math.ceil(targetDt * resolvedSpeed / (this.cfl * Math.min(dx, dy)))); if (substeps > 512) throw new NumericalFailure("stability_limit", "PIC/FLIP motion requires too many internal substeps", "particle-advection", {required_substeps: substeps, maximum_substeps: 512, maximum_particle_speed: maximum, maximum_wall_speed: wallSpeed, maximum_geometry_sweep_speed: sweepSpeed}); const dt = targetDt / substeps; const effectiveBlend = this.settlingSteps > 0 ? Math.min(this.blend, 0.05) : this.blend; const warnings: string[] = []; let gridEvidence: FailureEvidence = {};
    try {
      for (let step = 0; step < substeps; step += 1) {
        const fraction = (step + 1) / substeps; const subControl: ControlState = {time: saved.grid.time + fraction * targetDt, angleDegrees: saved.grid.control.angleDegrees + fraction * (control.angleDegrees - saved.grid.control.angleDegrees), angularVelocityDegrees: control.angularVelocityDegrees}; const oldFaces = this.grid.checkpoint(this.oldFaces ?? undefined); this.oldFaces = oldFaces; const gridReport = this.grid.advanceProjected(subControl, dt); warnings.push(...gridReport.warnings); gridEvidence = gridReport.evidence; const newFaces = this.grid.checkpoint(this.newFaces ?? undefined); this.newFaces = newFaces;
        for (let index = 0; index < this.x.length; index += 1) { const oldX = this.x[index] ?? 0; const oldY = this.y[index] ?? 0; const oldSample = this.sampleFaces(oldFaces, oldX, oldY); const newSample = this.sampleFaces(newFaces, oldX, oldY); const flipX = (this.vx[index] ?? 0) + newSample[0] - oldSample[0]; const flipY = (this.vy[index] ?? 0) + newSample[1] - oldSample[1]; this.vx[index] = effectiveBlend * flipX + (1 - effectiveBlend) * newSample[0]; this.vy[index] = effectiveBlend * flipY + (1 - effectiveBlend) * newSample[1]; const midpoint = this.sampleFaces(newFaces, oldX + 0.5 * dt * newSample[0], oldY + 0.5 * dt * newSample[1]); this.x[index] = oldX + dt * midpoint[0]; this.y[index] = oldY + dt * midpoint[1]; this.applyParticleBoundary(index, oldX, oldY, subControl, newFaces); }
        const transferred = this.scatterToFaces(newFaces); const outcome = this.grid.importFaces(transferred.u, transferred.v, subControl.time, subControl); if (outcome.status === "rejected") throw new NumericalFailure("projection_failure", `PIC/FLIP grid transfer rejected: ${outcome.reason}`);
      }
      this.advanceCount += 1; const populationInterval = scenario.solverOptions.picPopulationInterval ?? 8; if (this.advanceCount % populationInterval === 0) { const faces = this.grid.checkpoint(this.populationFaces ?? undefined); this.populationFaces = faces; this.maintainPopulation(control, faces); const transferred = this.scatterToFaces(faces); const outcome = this.grid.importFaces(transferred.u, transferred.v, faces.time, control); if (outcome.status === "rejected") throw new NumericalFailure("projection_failure", `PIC/FLIP population transfer rejected: ${outcome.reason}`); }
      if (this.settlingSteps > 0) this.settlingSteps -= 1;
    } catch (error) { this.restore(saved); throw error; }
    const state = this.grid.exportState(); maximum = 0; for (let index = 0; index < state.velocity.length; index += 2) maximum = Math.max(maximum, Math.hypot(state.velocity[index] ?? 0, state.velocity[index + 1] ?? 0)); const acceptedCfl = dt * Math.max(maximum, wallSpeed, sweepSpeed) / Math.min(dx, dy); if (acceptedCfl > this.cfl * (1 + 1e-6)) { this.restore(saved); throw new NumericalFailure("stability_limit", "post-step PIC/FLIP motion exceeded the selected substep envelope", "particle-advection", {accepted_cfl: acceptedCfl, maximum_cfl: this.cfl, substeps}); } let population: Readonly<Record<string, number>>; try { population = this.populationEvidence(control.angleDegrees); } catch (error) { this.restore(saved); throw error; } this.revision += 1; return {requestedDt: targetDt, advancedDt: targetDt, substeps, maxSpeed: maximum, stateRevision: this.revision, evidence: {...gridEvidence, maximum_wall_speed: wallSpeed, maximum_geometry_sweep_speed: sweepSpeed, maximum_particle_cfl: acceptedCfl, particle_count: this.x.length, unsupported_face_fraction: this.unsupportedFaceFraction, ...population, requested_reynolds: this.reynolds, effective_reynolds: this.reynolds, degraded_motion: wallSpeed === 0 && Math.abs(control.angleDegrees - saved.grid.control.angleDegrees) > 1e-9}, warnings: [...new Set(warnings)]};
  }

  public sampleVelocity(points: FloatArray): FloatArray { return this.grid.sampleVelocity(points); }
  public exportState(): CanonicalFlowState { return {...this.grid.exportState(), sourceSolver: this.info.id}; }
  public importState(state: CanonicalFlowState, control: ControlState): ImportOutcome { const saved = this.transactionCheckpoint(); const outcome = this.grid.importState(state, control); if (outcome.status === "rejected") return outcome; try { this.seedParticles(control.angleDegrees); this.settlingSteps = 1; } catch (error) { this.restore(saved); throw error; } this.revision += 1; return acceptedImport([...outcome.discardedState, "solver particles", "FLIP deltas"], ["solver particles reseeded; first step is PIC-dominant"]); }
  public diagnostics(): Diagnostics { const diagnostics = this.grid.diagnostics(); return {stateRevision: this.revision, values: {...diagnostics.values, particle_count: this.x.length, pic_flip_blend: this.blend}, warnings: diagnostics.warnings}; }
}
