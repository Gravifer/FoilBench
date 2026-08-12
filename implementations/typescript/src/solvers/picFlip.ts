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
  private collisionRadius = 1;
  private nx = 0; private ny = 0; private dx = 1; private dy = 1; private boundsX0 = 0; private boundsY0 = 0; private periodicX = false; private periodicY = false;
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
    const layout = dimensions(scenario.domain); const bounds = bounds2d(scenario.domain); this.nx = layout.nx; this.ny = layout.ny; this.dx = layout.dx; this.dy = layout.dy; this.boundsX0 = bounds.x[0]; this.boundsY0 = bounds.y[0]; this.periodicX = scenario.domain.periodicAxes.includes("x"); this.periodicY = scenario.domain.periodicAxes.includes("y"); const maximumCamber = Number(scenario.foil.naca[0]) / 100; const thickness = Number(scenario.foil.naca.slice(2)) / 100; this.collisionRadius = Math.hypot(0.75 * scenario.foil.chord, (maximumCamber + 0.51 * thickness) * scenario.foil.chord);
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
    const faces = this.grid.checkpoint(); for (let index = 0; index < xs.length; index += 1) { const px = this.x[index] ?? 0; const py = this.y[index] ?? 0; this.vx[index] = this.sampleFaceU(faces, px, py); this.vy[index] = this.sampleFaceV(faces, px, py); }
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
    const wx0 = this.quadraticWeight(gx - baseX); const wx1 = this.quadraticWeight(gx - baseX - 1); const wx2 = this.quadraticWeight(gx - baseX - 2); const wy0 = this.quadraticWeight(gy - baseY); const wy1 = this.quadraticWeight(gy - baseY - 1); const wy2 = this.quadraticWeight(gy - baseY - 2);
    if (!periodicX && !periodicY && baseX >= 0 && baseX + 2 < width && baseY >= 0 && baseY + 2 < height) {
      const row0 = baseY * width + baseX; const row1 = row0 + width; const row2 = row1 + width;
      return wy0 * (wx0 * (field[row0] ?? 0) + wx1 * (field[row0 + 1] ?? 0) + wx2 * (field[row0 + 2] ?? 0)) + wy1 * (wx0 * (field[row1] ?? 0) + wx1 * (field[row1 + 1] ?? 0) + wx2 * (field[row1 + 2] ?? 0)) + wy2 * (wx0 * (field[row2] ?? 0) + wx1 * (field[row2 + 1] ?? 0) + wx2 * (field[row2 + 2] ?? 0));
    }
    for (let oy = 0; oy < 3; oy += 1) for (let ox = 0; ox < 3; ox += 1) {
      let ix = baseX + ox; let iy = baseY + oy; if (periodicX) ix = this.wrapped(ix, uniqueWidth); if (periodicY) iy = this.wrapped(iy, uniqueHeight); if (ix < 0 || ix >= width || iy < 0 || iy >= height) continue;
      const weightX = ox === 0 ? wx0 : ox === 1 ? wx1 : wx2; const weightY = oy === 0 ? wy0 : oy === 1 ? wy1 : wy2; const weight = weightX * weightY; value += weight * (field[iy * width + ix] ?? 0); total += weight;
    }
    return total > 1e-12 ? value / total : 0;
  }

  private sampleFaceU(faces: StableCheckpoint, px: number, py: number): number { return this.sampleComponent(faces.u, this.nx + 1, this.ny, (px - this.boundsX0) / this.dx, (py - this.boundsY0) / this.dy - 0.5, this.periodicX, this.periodicY, this.periodicX, false); }
  private sampleFaceV(faces: StableCheckpoint, px: number, py: number): number { return this.sampleComponent(faces.v, this.nx, this.ny + 1, (px - this.boundsX0) / this.dx - 0.5, (py - this.boundsY0) / this.dy, this.periodicX, this.periodicY, false, this.periodicY); }

  private scatterComponent(values: FloatArray, positionsX: FloatArray, positionsY: FloatArray, width: number, height: number, gxOffset: number, gyOffset: number, periodicX: boolean, periodicY: boolean, duplicateX: boolean, duplicateY: boolean, fallback: FloatArray): {readonly field: FloatArray; readonly unsupported: number} {
    const scenario = this.requireScenario(); const {dx, dy} = dimensions(scenario.domain); const {x: bx, y: by} = bounds2d(scenario.domain); const output = allocate(scenario.precision, width * height); const weights = allocate(scenario.precision, width * height); const uniqueWidth = duplicateX ? width - 1 : width; const uniqueHeight = duplicateY ? height - 1 : height;
    for (let particle = 0; particle < positionsX.length; particle += 1) {
      const gx = ((positionsX[particle] ?? 0) - bx[0]) / dx + gxOffset; const gy = ((positionsY[particle] ?? 0) - by[0]) / dy + gyOffset; const baseX = Math.floor(gx - 0.5); const baseY = Math.floor(gy - 0.5);
      const wx0 = this.quadraticWeight(gx - baseX); const wx1 = this.quadraticWeight(gx - baseX - 1); const wx2 = this.quadraticWeight(gx - baseX - 2); const wy0 = this.quadraticWeight(gy - baseY); const wy1 = this.quadraticWeight(gy - baseY - 1); const wy2 = this.quadraticWeight(gy - baseY - 2);
      if (!periodicX && !periodicY && baseX >= 0 && baseX + 2 < width && baseY >= 0 && baseY + 2 < height) {
        const particleValue = values[particle] ?? 0; const row0 = baseY * width + baseX; const row1 = row0 + width; const row2 = row1 + width; const w00 = wx0 * wy0; const w01 = wx1 * wy0; const w02 = wx2 * wy0; const w10 = wx0 * wy1; const w11 = wx1 * wy1; const w12 = wx2 * wy1; const w20 = wx0 * wy2; const w21 = wx1 * wy2; const w22 = wx2 * wy2;
        output[row0] = (output[row0] ?? 0) + w00 * particleValue; output[row0 + 1] = (output[row0 + 1] ?? 0) + w01 * particleValue; output[row0 + 2] = (output[row0 + 2] ?? 0) + w02 * particleValue; output[row1] = (output[row1] ?? 0) + w10 * particleValue; output[row1 + 1] = (output[row1 + 1] ?? 0) + w11 * particleValue; output[row1 + 2] = (output[row1 + 2] ?? 0) + w12 * particleValue; output[row2] = (output[row2] ?? 0) + w20 * particleValue; output[row2 + 1] = (output[row2 + 1] ?? 0) + w21 * particleValue; output[row2 + 2] = (output[row2 + 2] ?? 0) + w22 * particleValue;
        weights[row0] = (weights[row0] ?? 0) + w00; weights[row0 + 1] = (weights[row0 + 1] ?? 0) + w01; weights[row0 + 2] = (weights[row0 + 2] ?? 0) + w02; weights[row1] = (weights[row1] ?? 0) + w10; weights[row1 + 1] = (weights[row1 + 1] ?? 0) + w11; weights[row1 + 2] = (weights[row1 + 2] ?? 0) + w12; weights[row2] = (weights[row2] ?? 0) + w20; weights[row2 + 1] = (weights[row2 + 1] ?? 0) + w21; weights[row2 + 2] = (weights[row2 + 2] ?? 0) + w22; continue;
      }
      for (let oy = 0; oy < 3; oy += 1) for (let ox = 0; ox < 3; ox += 1) { let ix = baseX + ox; let iy = baseY + oy; if (periodicX) ix = this.wrapped(ix, uniqueWidth); if (periodicY) iy = this.wrapped(iy, uniqueHeight); if (ix < 0 || ix >= width || iy < 0 || iy >= height) continue; const weightX = ox === 0 ? wx0 : ox === 1 ? wx1 : wx2; const weightY = oy === 0 ? wy0 : oy === 1 ? wy1 : wy2; const weight = weightX * weightY; const index = iy * width + ix; output[index] = (output[index] ?? 0) + weight * (values[particle] ?? 0); weights[index] = (weights[index] ?? 0) + weight; }
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
    for (let attempt = 0; attempt < 32; attempt += 1) { const px = periodicX ? bx[0] + this.rng.nextFloat32() * (bx[1] - bx[0]) : bx[0] + (0.1 + 0.8 * this.rng.nextFloat32()) * dx; const py = by[0] + this.rng.nextFloat32() * (by[1] - by[0]); if (foil.signedDistance(px, py, angleDegrees) > 0) { this.x[index] = px; this.y[index] = py; this.vx[index] = this.sampleFaceU(faces, px, py); this.vy[index] = this.sampleFaceV(faces, px, py); this.generations[index] = (this.generations[index] ?? 0) + 1; return; } }
    throw new NumericalFailure("transfer_failure", "PIC/FLIP could not respawn a solver particle", "population-maintenance", {particle_index: index, angle_degrees: angleDegrees});
  }

  private applyParticleBoundary(index: number, oldX: number, oldY: number, control: ControlState, faces: StableCheckpoint): void {
    const scenario = this.requireScenario(); const foil = this.requireFoil(); const {dx, dy} = dimensions(scenario.domain); const {x: bx, y: by} = bounds2d(scenario.domain); const periodicX = scenario.domain.periodicAxes.includes("x"); const periodicY = scenario.domain.periodicAxes.includes("y"); let px = this.x[index] ?? 0; let py = this.y[index] ?? 0;
    if (periodicX) px = bx[0] + (((px - bx[0]) % (bx[1] - bx[0])) + (bx[1] - bx[0])) % (bx[1] - bx[0]);
    if (periodicY) py = by[0] + (((py - by[0]) % (by[1] - by[0])) + (by[1] - by[0])) % (by[1] - by[0]);
    this.x[index] = px; this.y[index] = py;
    if ((!periodicX && (px < bx[0] || px >= bx[1])) || (!periodicY && (py < by[0] || py >= by[1]))) { this.respawnAtInlet(index, control.angleDegrees, faces); return; }
    const pivotX = scenario.foil.pivot[0] ?? 0; const pivotY = scenario.foil.pivot[1] ?? 0; const segmentX = px - oldX; const segmentY = py - oldY; const segmentLengthSquared = segmentX * segmentX + segmentY * segmentY; const closestFraction = segmentLengthSquared > 1e-12 ? Math.max(0, Math.min(1, ((pivotX - oldX) * segmentX + (pivotY - oldY) * segmentY) / segmentLengthSquared)) : 0; const closestX = oldX + closestFraction * segmentX; const closestY = oldY + closestFraction * segmentY; const broadRadius = this.collisionRadius + 0.05 * Math.min(dx, dy); if ((closestX - pivotX) ** 2 + (closestY - pivotY) ** 2 > broadRadius * broadRadius) return;
    const middleDistance = foil.signedDistance(0.5 * (oldX + px), 0.5 * (oldY + py), control.angleDegrees); const distance = foil.signedDistance(px, py, control.angleDegrees); if (middleDistance > 0 && distance > 0) return; if (distance > 0) { this.respawnAtInlet(index, control.angleDegrees, faces); return; }
    const normal = foil.normal(px, py, control.angleDegrees); const norm = Math.hypot(normal[0], normal[1]); if (distance >= -1.5 * Math.min(dx, dy) && normal.every(Number.isFinite) && norm > 0.5) { const nx = normal[0] / norm; const ny = normal[1] / norm; const correction = -distance + 1e-3 * Math.min(dx, dy); this.x[index] = px + correction * nx; this.y[index] = py + correction * ny; const omega = control.angularVelocityDegrees * Math.PI / 180; const wallX = -omega * (py - pivotY); const wallY = omega * (px - pivotX); const relativeNormal = ((this.vx[index] ?? 0) - wallX) * nx + ((this.vy[index] ?? 0) - wallY) * ny; if (relativeNormal < 0) { this.vx[index] = (this.vx[index] ?? 0) - relativeNormal * nx; this.vy[index] = (this.vy[index] ?? 0) - relativeNormal * ny; } }
    else this.respawnAtInlet(index, control.angleDegrees, faces);
    if (foil.signedDistance(this.x[index] ?? 0, this.y[index] ?? 0, control.angleDegrees) <= 0) this.respawnAtInlet(index, control.angleDegrees, faces);
  }

  private maintainPopulation(control: ControlState, faces: StableCheckpoint): void {
    const scenario = this.requireScenario(); const foil = this.requireFoil(); const {nx, ny, dx, dy} = dimensions(scenario.domain); const {x: bx, y: by} = bounds2d(scenario.domain); const counts = new Int32Array(nx * ny); const cells = new Int32Array(this.x.length); cells.fill(-1);
    for (let index = 0; index < this.x.length; index += 1) { const cx = Math.floor(((this.x[index] ?? bx[0]) - bx[0]) / dx); const cy = Math.floor(((this.y[index] ?? by[0]) - by[0]) / dy); if (cx >= 0 && cx < nx && cy >= 0 && cy < ny && foil.signedDistance(this.x[index] ?? 0, this.y[index] ?? 0, control.angleDegrees) > 0) { const cell = cy * nx + cx; cells[index] = cell; counts[cell] = (counts[cell] ?? 0) + 1; } }
    const targets: number[] = []; for (let cy = 0; cy < ny; cy += 1) for (let cx = 0; cx < nx; cx += 1) { const cell = cy * nx + cx; if (foil.signedDistance(bx[0] + (cx + 0.5) * dx, by[0] + (cy + 0.5) * dy, control.angleDegrees) <= 0) continue; for (let missing = counts[cell] ?? 0; missing < 4; missing += 1) targets.push(cell); }
    const donors: number[] = []; const retained = counts.slice(); for (let index = 0; index < cells.length; index += 1) { const cell = cells[index] ?? -1; if (cell < 0 || (retained[cell] ?? 0) > 4) { donors.push(index); if (cell >= 0) retained[cell] = (retained[cell] ?? 0) - 1; } }
    const moves = Math.min(targets.length, donors.length); for (let move = 0; move < moves; move += 1) { const index = donors[move] ?? 0; const cell = targets[move] ?? 0; const cx = cell % nx; const cy = Math.floor(cell / nx); for (let attempt = 0; attempt < 16; attempt += 1) { const px = bx[0] + (cx + 0.1 + 0.8 * this.rng.nextFloat32()) * dx; const py = by[0] + (cy + 0.1 + 0.8 * this.rng.nextFloat32()) * dy; if (foil.signedDistance(px, py, control.angleDegrees) > 0) { this.x[index] = px; this.y[index] = py; this.vx[index] = this.sampleFaceU(faces, px, py); this.vy[index] = this.sampleFaceV(faces, px, py); this.generations[index] = (this.generations[index] ?? 0) + 1; break; } } }
  }

  private populationEvidence(solid: Uint8Array): Readonly<Record<string, number>> {
    const scenario = this.requireScenario(); const {nx, ny, dx, dy} = dimensions(scenario.domain); const {x: bx, y: by} = bounds2d(scenario.domain); if (solid.length !== nx * ny) throw new NumericalFailure("transfer_failure", "PIC/FLIP solid mask has the wrong shape", "postcondition"); if (this.x.length !== this.y.length || this.x.length !== this.vx.length || this.x.length !== this.vy.length || this.x.length !== this.generations.length) throw new NumericalFailure("transfer_failure", "PIC/FLIP particle arrays disagree in length", "postcondition"); const counts = new Uint32Array(nx * ny);
    for (let index = 0; index < this.x.length; index += 1) { const px = this.x[index] ?? Number.NaN; const py = this.y[index] ?? Number.NaN; const ux = this.vx[index] ?? Number.NaN; const uy = this.vy[index] ?? Number.NaN; if (![px, py, ux, uy].every(Number.isFinite)) throw new NumericalFailure("nonfinite_state", "PIC/FLIP particle state became non-finite", "postcondition", {particle_index: index}); const cx = Math.floor((px - bx[0]) / dx); const cy = Math.floor((py - by[0]) / dy); if (cx >= 0 && cx < nx && cy >= 0 && cy < ny) counts[cy * nx + cx] = (counts[cy * nx + cx] ?? 0) + 1; }
    let fluidCells = 0; let emptyCells = 0; let underfilledCells = 0; let maximumCellPopulation = 0; for (let cell = 0; cell < counts.length; cell += 1) { if ((solid[cell] ?? 0) !== 0) continue; fluidCells += 1; const count = counts[cell] ?? 0; if (count === 0) emptyCells += 1; if (count < 4) underfilledCells += 1; maximumCellPopulation = Math.max(maximumCellPopulation, count); }
    return {empty_cell_fraction: emptyCells / Math.max(1, fluidCells), underfilled_cell_fraction: underfilledCells / Math.max(1, fluidCells), maximum_cell_population: maximumCellPopulation, unresolved_solid_particles: 0};
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
    let minimumSubsteps = 1; let stabilityRetries = 0;
    for (;;) {
      try { return this.advanceOnce(control, targetDt, minimumSubsteps, stabilityRetries); }
      catch (error) {
        if (!(error instanceof NumericalFailure) || error.reason !== "stability_limit") throw error;
        const accepted = Number(error.evidence["accepted_cfl"]); const maximumAllowed = Number(error.evidence["maximum_cfl"]); const attempted = Number(error.evidence["substeps"]);
        if (!(Number.isFinite(accepted) && Number.isFinite(maximumAllowed) && Number.isInteger(attempted) && accepted > maximumAllowed && maximumAllowed > 0)) throw error;
        const nextSubsteps = Math.max(attempted + 1, Math.ceil(1.05 * attempted * accepted / maximumAllowed));
        if (nextSubsteps > 512) throw new NumericalFailure("stability_limit", "PIC/FLIP retry requires too many internal substeps", "particle-advection", {...error.evidence, required_substeps: nextSubsteps, maximum_substeps: 512, stability_retries: stabilityRetries});
        stabilityRetries += 1; minimumSubsteps = nextSubsteps;
      }
    }
  }

  private advanceOnce(control: ControlState, targetDt: number, minimumSubsteps: number, stabilityRetries: number): StepReport {
    if (!(targetDt > 0) || !Number.isFinite(targetDt)) throw new RangeError("target dt must be finite and positive"); const scenario = this.requireScenario(); const gridState = this.grid.exportState(); const expectedTime = gridState.time + targetDt; const timeTolerance = scenario.precision === "float32" ? 1e-6 : 1e-12; if (!Number.isFinite(control.time) || Math.abs(control.time - expectedTime) > timeTolerance * Math.max(1, Math.abs(expectedTime))) throw new NumericalFailure("time_contract_failure", "control completion time disagrees with target interval", "time-mapping", {expected_time: expectedTime, control_time: control.time, target_dt: targetDt}); const saved = this.transactionCheckpoint(); const {dx, dy} = dimensions(scenario.domain); let maximum = 0; for (let index = 0; index < this.x.length; index += 1) maximum = Math.max(maximum, Math.hypot(this.vx[index] ?? 0, this.vy[index] ?? 0)); for (let index = 0; index < gridState.velocity.length; index += 2) maximum = Math.max(maximum, Math.hypot(gridState.velocity[index] ?? 0, gridState.velocity[index + 1] ?? 0)); const wallSpeed = Math.abs(control.angularVelocityDegrees) * Math.PI / 180 * scenario.foil.chord; const sweepSpeed = scenario.foil.chord * Math.abs(control.angleDegrees - saved.grid.control.angleDegrees) * Math.PI / (180 * targetDt); const resolvedSpeed = Math.max(maximum, wallSpeed, sweepSpeed, 1e-6); const substeps = Math.max(minimumSubsteps, Math.ceil(targetDt * resolvedSpeed / (this.cfl * Math.min(dx, dy)))); if (substeps > 512) throw new NumericalFailure("stability_limit", "PIC/FLIP motion requires too many internal substeps", "particle-advection", {required_substeps: substeps, maximum_substeps: 512, maximum_particle_speed: maximum, maximum_wall_speed: wallSpeed, maximum_geometry_sweep_speed: sweepSpeed}); const dt = targetDt / substeps; const effectiveBlend = this.settlingSteps > 0 ? Math.min(this.blend, 0.05) : this.blend; const warnings: string[] = []; let gridEvidence: FailureEvidence = {};
    let finalFaces: StableCheckpoint | null = null;
    try {
      for (let step = 0; step < substeps; step += 1) {
        const fraction = (step + 1) / substeps; const subControl: ControlState = {time: saved.grid.time + fraction * targetDt, angleDegrees: saved.grid.control.angleDegrees + fraction * (control.angleDegrees - saved.grid.control.angleDegrees), angularVelocityDegrees: control.angularVelocityDegrees}; const fallbackFaces = this.grid.checkpoint(this.oldFaces ?? undefined); this.oldFaces = fallbackFaces; const transferred = this.scatterToFaces(fallbackFaces); this.grid.stageParticleFaces(transferred.u, transferred.v); const preProjection = this.grid.checkpoint(this.populationFaces ?? undefined); this.populationFaces = preProjection; const gridReport = this.grid.advanceProjected(subControl, dt); warnings.push(...gridReport.warnings); gridEvidence = gridReport.evidence; const newFaces = this.grid.checkpoint(this.newFaces ?? undefined); this.newFaces = newFaces; finalFaces = newFaces;
        for (let index = 0; index < this.x.length; index += 1) { const oldX = this.x[index] ?? 0; const oldY = this.y[index] ?? 0; const oldU = this.sampleFaceU(preProjection, oldX, oldY); const oldV = this.sampleFaceV(preProjection, oldX, oldY); const newU = this.sampleFaceU(newFaces, oldX, oldY); const newV = this.sampleFaceV(newFaces, oldX, oldY); const flipX = (this.vx[index] ?? 0) + newU - oldU; const flipY = (this.vy[index] ?? 0) + newV - oldV; this.vx[index] = effectiveBlend * flipX + (1 - effectiveBlend) * newU; this.vy[index] = effectiveBlend * flipY + (1 - effectiveBlend) * newV; const midpointX = oldX + 0.5 * dt * newU; const midpointY = oldY + 0.5 * dt * newV; this.x[index] = oldX + dt * this.sampleFaceU(newFaces, midpointX, midpointY); this.y[index] = oldY + dt * this.sampleFaceV(newFaces, midpointX, midpointY); this.applyParticleBoundary(index, oldX, oldY, subControl, newFaces); }
      }
      this.advanceCount += 1; const populationInterval = scenario.solverOptions.picPopulationInterval ?? 8; if (this.advanceCount % populationInterval === 0) { const faces = this.grid.checkpoint(this.populationFaces ?? undefined); this.populationFaces = faces; this.maintainPopulation(control, faces); }
      if (this.settlingSteps > 0) this.settlingSteps -= 1;
    } catch (error) { this.restore(saved); throw error; }
    const state = this.grid.exportState(); maximum = 0; for (let index = 0; index < state.velocity.length; index += 2) maximum = Math.max(maximum, Math.hypot(state.velocity[index] ?? 0, state.velocity[index + 1] ?? 0)); let particleMaximum = 0; for (let index = 0; index < this.vx.length; index += 1) particleMaximum = Math.max(particleMaximum, Math.hypot(this.vx[index] ?? 0, this.vy[index] ?? 0)); maximum = Math.max(maximum, particleMaximum); const acceptedCfl = dt * Math.max(maximum, wallSpeed, sweepSpeed) / Math.min(dx, dy); if (acceptedCfl > this.cfl * (1 + 1e-6)) { this.restore(saved); throw new NumericalFailure("stability_limit", "post-step PIC/FLIP motion exceeded the selected substep envelope", "particle-advection", {accepted_cfl: acceptedCfl, maximum_cfl: this.cfl, substeps}); } if (finalFaces === null) { this.restore(saved); throw new NumericalFailure("postcondition_failure", "PIC/FLIP completed without a projected solid mask", "postcondition"); } let population: Readonly<Record<string, number>>; try { population = this.populationEvidence(finalFaces.solid); } catch (error) { this.restore(saved); throw error; } this.revision += 1; return {requestedDt: targetDt, advancedDt: targetDt, substeps, maxSpeed: maximum, stateRevision: this.revision, evidence: {...gridEvidence, stability_retries: stabilityRetries, maximum_particle_speed: particleMaximum, maximum_wall_speed: wallSpeed, maximum_geometry_sweep_speed: sweepSpeed, maximum_particle_cfl: acceptedCfl, particle_count: this.x.length, unsupported_face_fraction: this.unsupportedFaceFraction, ...population, requested_reynolds: this.reynolds, effective_reynolds: this.reynolds, degraded_motion: wallSpeed === 0 && Math.abs(control.angleDegrees - saved.grid.control.angleDegrees) > 1e-9}, warnings: [...new Set(warnings)]};
  }

  public sampleVelocity(points: FloatArray): FloatArray { return this.grid.sampleVelocity(points); }
  public exportState(): CanonicalFlowState { return {...this.grid.exportState(), sourceSolver: this.info.id}; }
  public importState(state: CanonicalFlowState, control: ControlState): ImportOutcome { const saved = this.transactionCheckpoint(); const outcome = this.grid.importState(state, control); if (outcome.status === "rejected") return outcome; try { this.seedParticles(control.angleDegrees); this.settlingSteps = 1; } catch (error) { this.restore(saved); throw error; } this.revision += 1; return acceptedImport([...outcome.discardedState, "solver particles", "FLIP deltas"], ["solver particles reseeded; first step is PIC-dominant"]); }
  public diagnostics(): Diagnostics { const diagnostics = this.grid.diagnostics(); return {stateRevision: this.revision, values: {...diagnostics.values, particle_count: this.x.length, pic_flip_blend: this.blend}, warnings: diagnostics.warnings}; }
}
