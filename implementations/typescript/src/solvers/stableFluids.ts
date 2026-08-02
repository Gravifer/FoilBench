import type {CanonicalFlowState, ControlState, Diagnostics, FlowSolver, FloatArray, ImportOutcome, Scenario, SolverInfo, StepReport} from "../core/contracts.js";
import {NumericalFailure} from "../core/contracts.js";
import {NacaFoil} from "../core/geometry.js";
import {allocate, bounds2d, cellToFaces, cellVelocity, dimensions, project, sampleCell} from "../core/grid.js";
import {fieldDiagnostics} from "../core/metrics.js";

type TransportMode = "maccormack" | "semi-lagrangian" | "skew-rk2";

export class StableFluidsSolver implements FlowSolver {
  public readonly info: SolverInfo = {id: "stable-fluids", displayName: "Stable Fluids (MAC)", dimensions: [2], supportsMovingBoundary: true, acceleration: "typed-arrays"};
  public reynolds = 1;
  public transportMode: TransportMode = "maccormack";
  private scenario: Scenario | null = null;
  private foil: NacaFoil | null = null;
  private u: FloatArray = new Float32Array();
  private v: FloatArray = new Float32Array();
  private solid = new Uint8Array();
  private time = 0;
  private control: ControlState = {time: 0, angleDegrees: 0, angularVelocityDegrees: 0};

  public initialize(scenario: Scenario, seed: number): void {
    void seed;
    if (scenario.domain.dimension !== 2) throw new RangeError("stable-fluids supports only 2D");
    this.scenario = scenario; this.foil = new NacaFoil(scenario.foil); this.reynolds = scenario.reynolds; this.time = 0;
    this.transportMode = scenario.solverOptions.stableAdvection ?? "maccormack";
    this.control = {time: 0, angleDegrees: scenario.controls[0]?.angleDegrees ?? 0, angularVelocityDegrees: 0};
    const {nx, ny, dx, dy} = dimensions(scenario.domain); const velocity = allocate(scenario.precision, nx * ny * 2); const {x: bx, y: by} = bounds2d(scenario.domain);
    for (let y = 0; y < ny; y += 1) for (let x = 0; x < nx; x += 1) {
      const index = y * nx + x; const px = bx[0] + (x + 0.5) * dx; const py = by[0] + (y + 0.5) * dy;
      if (scenario.solverOptions.initialCondition === "taylor-green") { velocity[2 * index] = Math.sin(px) * Math.cos(py); velocity[2 * index + 1] = -Math.cos(px) * Math.sin(py); }
      else if (scenario.solverOptions.initialCondition === "poiseuille") { const center = 0.5 * (by[0] + by[1]); const radius = 0.5 * (by[1] - by[0]); velocity[2 * index] = 1.5 * (1 - ((py - center) / radius) ** 2); }
      else { velocity[2 * index] = scenario.freestream[0] ?? 0; velocity[2 * index + 1] = scenario.freestream[1] ?? 0; }
    }
    const periodicX = scenario.domain.periodicAxes.includes("x"); const periodicY = scenario.domain.periodicAxes.includes("y");
    ({u: this.u, v: this.v} = cellToFaces(velocity, nx, ny, scenario.precision, periodicX, periodicY));
    this.solid = new Uint8Array(nx * ny); this.updateSolid(this.control); this.enforceSolidFaces(this.control);
    project(this.u, this.v, this.solid, nx, ny, dx, dy, scenario.precision, scenario.solverOptions.pressureMaxIterations ?? 640, scenario.solverOptions.pressureTolerance ?? 1e-5, periodicX, periodicY);
  }

  public setReynolds(reynolds: number): void { if (!(reynolds > 0) || !Number.isFinite(reynolds)) throw new RangeError("Reynolds must be finite and positive"); this.reynolds = reynolds; }
  public setTransportMode(mode: TransportMode): void { this.transportMode = mode; }
  private requireScenario(): Scenario { if (this.scenario === null) throw new Error("solver is not initialized"); return this.scenario; }

  private updateSolid(control: ControlState): void {
    const scenario = this.requireScenario(); const foil = this.foil; if (foil === null) throw new Error("foil is missing");
    const {nx, ny, dx, dy} = dimensions(scenario.domain); const {x: bx, y: by} = bounds2d(scenario.domain);
    for (let y = 0; y < ny; y += 1) for (let x = 0; x < nx; x += 1) this.solid[y * nx + x] = foil.signedDistance(bx[0] + (x + 0.5) * dx, by[0] + (y + 0.5) * dy, control.angleDegrees) <= 0 ? 1 : 0;
  }

  private enforceSolidFaces(control: ControlState): void {
    const scenario = this.requireScenario(); const {nx, ny, dx, dy} = dimensions(scenario.domain); const {x: bx, y: by} = bounds2d(scenario.domain); const pivotX = scenario.foil.pivot[0] ?? 0; const pivotY = scenario.foil.pivot[1] ?? 0; const omega = control.angularVelocityDegrees * Math.PI / 180;
    for (let y = 0; y < ny; y += 1) for (let x = 0; x <= nx; x += 1) { const leftSolid = x > 0 && this.solid[y * nx + x - 1] !== 0; const rightSolid = x < nx && this.solid[y * nx + x] !== 0; if (leftSolid || rightSolid) { const py = by[0] + (y + 0.5) * dy; this.u[y * (nx + 1) + x] = -omega * (py - pivotY); } }
    for (let y = 0; y <= ny; y += 1) for (let x = 0; x < nx; x += 1) { const bottomSolid = y > 0 && this.solid[(y - 1) * nx + x] !== 0; const topSolid = y < ny && this.solid[y * nx + x] !== 0; if (bottomSolid || topSolid) { const px = bx[0] + (x + 0.5) * dx; this.v[y * nx + x] = omega * (px - pivotX); } }
  }

  private backtrace(velocity: FloatArray, px: number, py: number, dt: number): readonly [number, number] {
    const scenario = this.requireScenario(); const first = sampleCell(velocity, scenario.domain, px, py); const midpointX = px - 0.5 * dt * first[0]; const midpointY = py - 0.5 * dt * first[1]; const midpoint = sampleCell(velocity, scenario.domain, midpointX, midpointY); return [px - dt * midpoint[0], py - dt * midpoint[1]];
  }

  private semiLagrangian(velocity: FloatArray, dt: number): FloatArray {
    const scenario = this.requireScenario(); const {nx, ny, dx, dy} = dimensions(scenario.domain); const {x: bx, y: by} = bounds2d(scenario.domain); const output = allocate(scenario.precision, velocity.length);
    for (let y = 0; y < ny; y += 1) for (let x = 0; x < nx; x += 1) { const px = bx[0] + (x + 0.5) * dx; const py = by[0] + (y + 0.5) * dy; const departure = this.backtrace(velocity, px, py, dt); const sampled = sampleCell(velocity, scenario.domain, departure[0], departure[1]); const cell = y * nx + x; output[2 * cell] = sampled[0]; output[2 * cell + 1] = sampled[1]; }
    return output;
  }

  private maccormack(velocity: FloatArray, dt: number): FloatArray {
    const scenario = this.requireScenario(); const {nx, ny} = dimensions(scenario.domain); const first = this.semiLagrangian(velocity, dt); const reverse = this.semiLagrangian(first, -dt); const output = allocate(scenario.precision, velocity.length); const periodicX = scenario.domain.periodicAxes.includes("x"); const periodicY = scenario.domain.periodicAxes.includes("y");
    for (let y = 0; y < ny; y += 1) for (let x = 0; x < nx; x += 1) for (let component = 0; component < 2; component += 1) { let lower = Number.POSITIVE_INFINITY; let upper = Number.NEGATIVE_INFINITY; for (let oy = -1; oy <= 1; oy += 1) for (let ox = -1; ox <= 1; ox += 1) { let sx = x + ox; let sy = y + oy; if (periodicX) sx = (sx + nx) % nx; else sx = Math.max(0, Math.min(nx - 1, sx)); if (periodicY) sy = (sy + ny) % ny; else sy = Math.max(0, Math.min(ny - 1, sy)); const value = velocity[2 * (sy * nx + sx) + component] ?? 0; lower = Math.min(lower, value); upper = Math.max(upper, value); } const index = 2 * (y * nx + x) + component; const corrected = (first[index] ?? 0) + 0.5 * ((velocity[index] ?? 0) - (reverse[index] ?? 0)); output[index] = Math.max(Math.min(lower, first[index] ?? 0), Math.min(Math.max(upper, first[index] ?? 0), corrected)); }
    return output;
  }

  private skewConvection(velocity: FloatArray): FloatArray {
    const scenario = this.requireScenario(); const {nx, ny, dx, dy} = dimensions(scenario.domain); const output = allocate(scenario.precision, velocity.length); const periodicX = scenario.domain.periodicAxes.includes("x"); const periodicY = scenario.domain.periodicAxes.includes("y"); const cell = (x: number, y: number, component: number): number => { const sx = periodicX ? (x + nx) % nx : Math.max(0, Math.min(nx - 1, x)); const sy = periodicY ? (y + ny) % ny : Math.max(0, Math.min(ny - 1, y)); return velocity[2 * (sy * nx + sx) + component] ?? 0; };
    for (let y = 0; y < ny; y += 1) for (let x = 0; x < nx; x += 1) { const u = cell(x, y, 0); const v = cell(x, y, 1); const duDx = (cell(x + 1, y, 0) - cell(x - 1, y, 0)) / (2 * dx); const duDy = (cell(x, y + 1, 0) - cell(x, y - 1, 0)) / (2 * dy); const dvDx = (cell(x + 1, y, 1) - cell(x - 1, y, 1)) / (2 * dx); const dvDy = (cell(x, y + 1, 1) - cell(x, y - 1, 1)) / (2 * dy); const duuDx = (cell(x + 1, y, 0) ** 2 - cell(x - 1, y, 0) ** 2) / (2 * dx); const duvDy = (cell(x, y + 1, 0) * cell(x, y + 1, 1) - cell(x, y - 1, 0) * cell(x, y - 1, 1)) / (2 * dy); const duvDx = (cell(x + 1, y, 0) * cell(x + 1, y, 1) - cell(x - 1, y, 0) * cell(x - 1, y, 1)) / (2 * dx); const dvvDy = (cell(x, y + 1, 1) ** 2 - cell(x, y - 1, 1) ** 2) / (2 * dy); const index = 2 * (y * nx + x); output[index] = 0.5 * (u * duDx + v * duDy + duuDx + duvDy); output[index + 1] = 0.5 * (u * dvDx + v * dvDy + duvDx + dvvDy); }
    return output;
  }

  private skewRk2(velocity: FloatArray, dt: number): FloatArray {
    const scenario = this.requireScenario(); const first = this.skewConvection(velocity); const midpoint = allocate(scenario.precision, velocity.length); for (let index = 0; index < velocity.length; index += 1) midpoint[index] = (velocity[index] ?? 0) - 0.5 * dt * (first[index] ?? 0); const second = this.skewConvection(midpoint); const output = allocate(scenario.precision, velocity.length); for (let index = 0; index < velocity.length; index += 1) output[index] = (velocity[index] ?? 0) - dt * (second[index] ?? 0); return output;
  }

  private diffuse(velocity: FloatArray, dt: number): FloatArray {
    const scenario = this.requireScenario(); const {nx, ny, dx, dy} = dimensions(scenario.domain); const speed = Math.max(Math.hypot(scenario.freestream[0] ?? 0, scenario.freestream[1] ?? 0), 1); const viscosity = speed * scenario.foil.chord / this.reynolds; const ax = viscosity * dt / (dx * dx); const ay = viscosity * dt / (dy * dy); if (ax + ay < 1e-8) return velocity; const periodicX = scenario.domain.periodicAxes.includes("x"); const periodicY = scenario.domain.periodicAxes.includes("y"); let current: FloatArray = allocate(scenario.precision, velocity.length); current.set(velocity); let next: FloatArray = allocate(scenario.precision, velocity.length); const sample = (field: FloatArray, x: number, y: number, component: number): number => { const sx = periodicX ? (x + nx) % nx : Math.max(0, Math.min(nx - 1, x)); const sy = periodicY ? (y + ny) % ny : Math.max(0, Math.min(ny - 1, y)); return field[2 * (sy * nx + sx) + component] ?? 0; };
    for (let iteration = 0; iteration < 12; iteration += 1) { for (let y = 0; y < ny; y += 1) for (let x = 0; x < nx; x += 1) for (let component = 0; component < 2; component += 1) { const index = 2 * (y * nx + x) + component; next[index] = ((velocity[index] ?? 0) + ax * (sample(current, x - 1, y, component) + sample(current, x + 1, y, component)) + ay * (sample(current, x, y - 1, component) + sample(current, x, y + 1, component))) / (1 + 2 * ax + 2 * ay); } const swap = current; current = next; next = swap; }
    return current;
  }

  private substep(control: ControlState, dt: number): void {
    const scenario = this.requireScenario(); const {nx, ny, dx, dy} = dimensions(scenario.domain); const before = cellVelocity(this.u, this.v, nx, ny, scenario.precision); let advected = this.transportMode === "semi-lagrangian" ? this.semiLagrangian(before, dt) : this.transportMode === "skew-rk2" ? this.skewRk2(before, dt) : this.maccormack(before, dt); advected = this.diffuse(advected, dt); const periodicX = scenario.domain.periodicAxes.includes("x"); const periodicY = scenario.domain.periodicAxes.includes("y"); ({u: this.u, v: this.v} = cellToFaces(advected, nx, ny, scenario.precision, periodicX, periodicY)); this.updateSolid(control); this.enforceSolidFaces(control); if (!periodicX) { const inlet = scenario.freestream[0] ?? 0; for (let y = 0; y < ny; y += 1) this.u[y * (nx + 1)] = inlet; } project(this.u, this.v, this.solid, nx, ny, dx, dy, scenario.precision, scenario.solverOptions.pressureMaxIterations ?? 640, scenario.solverOptions.pressureTolerance ?? 1e-5, periodicX, periodicY); this.enforceSolidFaces(control);
  }

  public advance(control: ControlState, targetDt: number): StepReport {
    if (!(targetDt > 0) || !Number.isFinite(targetDt)) throw new RangeError("target dt must be finite and positive"); const scenario = this.requireScenario(); const {nx, ny, dx, dy} = dimensions(scenario.domain); const velocity = cellVelocity(this.u, this.v, nx, ny, scenario.precision); let maxSpeed = 0; for (let index = 0; index < velocity.length; index += 2) maxSpeed = Math.max(maxSpeed, Math.hypot(velocity[index] ?? 0, velocity[index + 1] ?? 0)); const configuredCfl = scenario.solverOptions.stableCfl ?? 0.7; const cfl = this.transportMode === "skew-rk2" ? Math.min(configuredCfl, 0.4) : configuredCfl; const substeps = Math.max(1, Math.ceil(targetDt * Math.max(maxSpeed, 1e-6) / (cfl * Math.min(dx, dy)))); const dt = targetDt / substeps; const savedU = this.u.slice(); const savedV = this.v.slice(); const savedSolid = this.solid.slice(); try { for (let step = 0; step < substeps; step += 1) this.substep(control, dt); if (!this.u.every(Number.isFinite) || !this.v.every(Number.isFinite)) throw new NumericalFailure("nonfinite_state", "stable-fluids produced non-finite velocity"); } catch (error) { this.u = savedU; this.v = savedV; this.solid = savedSolid; throw error; } this.time += targetDt; this.control = {...control, time: this.time}; return {requestedDt: targetDt, advancedDt: targetDt, substeps, maxSpeed, warnings: []};
  }

  public sampleVelocity(points: FloatArray): FloatArray { const scenario = this.requireScenario(); if (points.length % 2 !== 0) throw new RangeError("points must contain x/y pairs"); const velocity = cellVelocity(this.u, this.v, dimensions(scenario.domain).nx, dimensions(scenario.domain).ny, scenario.precision); const output = allocate(scenario.precision, points.length); for (let index = 0; index < points.length; index += 2) { const sampled = sampleCell(velocity, scenario.domain, points[index] ?? 0, points[index + 1] ?? 0); output[index] = sampled[0]; output[index + 1] = sampled[1]; } return output; }
  public exportState(): CanonicalFlowState { const scenario = this.requireScenario(); const {nx, ny} = dimensions(scenario.domain); return {schemaVersion: 1, dimension: 2, bounds: scenario.domain.bounds, resolution: scenario.domain.resolution, periodicAxes: scenario.domain.periodicAxes, time: this.time, precision: scenario.precision, angleDegrees: this.control.angleDegrees, angularVelocityDegrees: this.control.angularVelocityDegrees, sourceLanguage: "typescript", sourceSolver: this.info.id, velocity: cellVelocity(this.u, this.v, nx, ny, scenario.precision), density: null}; }
  public importState(state: CanonicalFlowState, control: ControlState): ImportOutcome { const scenario = this.requireScenario(); if (state.dimension !== 2 || state.resolution[0] !== scenario.domain.resolution[0] || state.resolution[1] !== scenario.domain.resolution[1]) return {status: "rejected", reason: "incompatible_domain", discardedState: [], warnings: []}; if (!state.velocity.every(Number.isFinite) || (state.density !== null && !state.density.every(Number.isFinite))) return {status: "rejected", reason: "nonfinite_state", discardedState: [], warnings: []}; const {nx, ny, dx, dy} = dimensions(scenario.domain); const periodicX = scenario.domain.periodicAxes.includes("x"); const periodicY = scenario.domain.periodicAxes.includes("y"); ({u: this.u, v: this.v} = cellToFaces(state.velocity, nx, ny, scenario.precision, periodicX, periodicY)); this.time = state.time; this.control = control; this.updateSolid(control); this.enforceSolidFaces(control); project(this.u, this.v, this.solid, nx, ny, dx, dy, scenario.precision, scenario.solverOptions.pressureMaxIterations ?? 640, scenario.solverOptions.pressureTolerance ?? 1e-5, periodicX, periodicY); this.enforceSolidFaces(control); if (!this.u.every(Number.isFinite) || !this.v.every(Number.isFinite)) return {status: "rejected", reason: "projection_failure", discardedState: [], warnings: []}; return {status: "accepted", reason: "none", discardedState: ["pressure history"], warnings: []}; }
  public diagnostics(): Diagnostics { const scenario = this.requireScenario(); const foil = this.foil; if (foil === null) throw new Error("foil is missing"); const {nx, ny} = dimensions(scenario.domain); const velocity = cellVelocity(this.u, this.v, nx, ny, scenario.precision); return {values: {...fieldDiagnostics(velocity, scenario, foil, this.control.angleDegrees), effective_reynolds: this.reynolds}, warnings: []}; }
}
