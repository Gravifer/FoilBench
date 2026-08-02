import type {CanonicalFlowState, ControlState, Diagnostics, FlowSolver, FloatArray, ImportOutcome, Scenario, SolverInfo, StepReport} from "../core/contracts.js";
import {NumericalFailure} from "../core/contracts.js";
import {NacaFoil} from "../core/geometry.js";
import {allocate, bounds2d, cellToFaces, cellVelocity, dimensions, divergence, project, sampleCell} from "../core/grid.js";

export class StableFluidsSolver implements FlowSolver {
  public readonly info: SolverInfo = {id: "stable-fluids", displayName: "Stable Fluids (MAC)", dimensions: [2], supportsMovingBoundary: true, acceleration: "typed-arrays"};
  public reynolds = 1;
  private scenario: Scenario | null = null; private foil: NacaFoil | null = null;
  private u: FloatArray = new Float32Array(); private v: FloatArray = new Float32Array(); private solid = new Uint8Array(); private time = 0; private control: ControlState = {time: 0, angleDegrees: 0, angularVelocityDegrees: 0};

  public initialize(scenario: Scenario, seed: number): void {
    void seed; if (scenario.domain.dimension !== 2) throw new RangeError("stable-fluids supports only 2D");
    this.scenario = scenario; this.foil = new NacaFoil(scenario.foil); this.reynolds = scenario.reynolds; this.time = 0;
    this.control = {time: 0, angleDegrees: scenario.controls[0]?.angleDegrees ?? 0, angularVelocityDegrees: 0};
    const {nx, ny, dx, dy} = dimensions(scenario.domain); const velocity = allocate(scenario.precision, nx * ny * 2);
    const {x: bx, y: by} = bounds2d(scenario.domain);
    for (let y = 0; y < ny; y += 1) for (let x = 0; x < nx; x += 1) {
      const index = y * nx + x; const px = bx[0] + (x + 0.5) * dx; const py = by[0] + (y + 0.5) * dy;
      if (scenario.solverOptions.initialCondition === "taylor-green") { velocity[2 * index] = Math.sin(px) * Math.cos(py); velocity[2 * index + 1] = -Math.cos(px) * Math.sin(py); }
      else if (scenario.solverOptions.initialCondition === "poiseuille") { const center = 0.5 * (by[0] + by[1]); const radius = 0.5 * (by[1] - by[0]); velocity[2 * index] = 1.5 * (1 - ((py - center) / radius) ** 2); velocity[2 * index + 1] = 0; }
      else { velocity[2 * index] = scenario.freestream[0] ?? 0; velocity[2 * index + 1] = scenario.freestream[1] ?? 0; }
    }
    ({u: this.u, v: this.v} = cellToFaces(velocity, nx, ny, scenario.precision)); this.solid = new Uint8Array(nx * ny); this.updateSolid(this.control);
    project(this.u, this.v, this.solid, nx, ny, dx, dy, scenario.precision, scenario.solverOptions.pressureMaxIterations ?? 640, scenario.solverOptions.pressureTolerance ?? 1e-5);
  }

  public setReynolds(reynolds: number): void { if (!(reynolds > 0) || !Number.isFinite(reynolds)) throw new RangeError("Reynolds must be finite and positive"); this.reynolds = reynolds; }

  private requireScenario(): Scenario { if (this.scenario === null) throw new Error("solver is not initialized"); return this.scenario; }
  private updateSolid(control: ControlState): void {
    const scenario = this.requireScenario(); const foil = this.foil; if (foil === null) throw new Error("foil is missing");
    const {nx, ny, dx, dy} = dimensions(scenario.domain); const {x: bx, y: by} = bounds2d(scenario.domain);
    for (let y = 0; y < ny; y += 1) for (let x = 0; x < nx; x += 1) this.solid[y * nx + x] = foil.signedDistance(bx[0] + (x + 0.5) * dx, by[0] + (y + 0.5) * dy, control.angleDegrees) <= 0 ? 1 : 0;
  }

  private substep(control: ControlState, dt: number): void {
    const scenario = this.requireScenario(); const {nx, ny, dx, dy} = dimensions(scenario.domain); const before = cellVelocity(this.u, this.v, nx, ny, scenario.precision); const advected = allocate(scenario.precision, before.length); const {x: bx, y: by} = bounds2d(scenario.domain);
    for (let y = 0; y < ny; y += 1) for (let x = 0; x < nx; x += 1) {
      const index = y * nx + x; const px = bx[0] + (x + 0.5) * dx; const py = by[0] + (y + 0.5) * dy; const velocity = sampleCell(before, scenario.domain, px, py); const sampled = sampleCell(before, scenario.domain, px - dt * velocity[0], py - dt * velocity[1]); advected[2 * index] = sampled[0]; advected[2 * index + 1] = sampled[1];
    }
    ({u: this.u, v: this.v} = cellToFaces(advected, nx, ny, scenario.precision)); this.updateSolid(control);
    for (let y = 0; y < ny; y += 1) for (let x = 0; x < nx; x += 1) if (this.solid[y * nx + x] !== 0) {
      this.u[y * (nx + 1) + x] = 0; this.u[y * (nx + 1) + x + 1] = 0; this.v[y * nx + x] = 0; this.v[(y + 1) * nx + x] = 0;
    }
    const inlet = scenario.freestream[0] ?? 0; for (let y = 0; y < ny; y += 1) this.u[y * (nx + 1)] = inlet;
    project(this.u, this.v, this.solid, nx, ny, dx, dy, scenario.precision, scenario.solverOptions.pressureMaxIterations ?? 640, scenario.solverOptions.pressureTolerance ?? 1e-5);
  }

  public advance(control: ControlState, targetDt: number): StepReport {
    if (!(targetDt > 0) || !Number.isFinite(targetDt)) throw new RangeError("target dt must be finite and positive"); const scenario = this.requireScenario(); const {nx, ny, dx, dy} = dimensions(scenario.domain); const velocity = cellVelocity(this.u, this.v, nx, ny, scenario.precision); let maxSpeed = 0;
    for (let index = 0; index < velocity.length; index += 2) maxSpeed = Math.max(maxSpeed, Math.hypot(velocity[index] ?? 0, velocity[index + 1] ?? 0));
    const cfl = scenario.solverOptions.stableCfl ?? 0.7; const substeps = Math.max(1, Math.ceil(targetDt * Math.max(maxSpeed, 1e-6) / (cfl * Math.min(dx, dy)))); const dt = targetDt / substeps;
    const savedU = this.u.slice(); const savedV = this.v.slice(); const savedSolid = this.solid.slice();
    try { for (let step = 0; step < substeps; step += 1) this.substep(control, dt); if (!this.u.every(Number.isFinite) || !this.v.every(Number.isFinite)) throw new NumericalFailure("nonfinite_state", "stable-fluids produced non-finite velocity"); }
    catch (error) { this.u = savedU; this.v = savedV; this.solid = savedSolid; throw error; }
    this.time += targetDt; this.control = {...control, time: this.time}; return {requestedDt: targetDt, advancedDt: targetDt, substeps, maxSpeed, warnings: []};
  }

  public sampleVelocity(points: FloatArray): FloatArray { const scenario = this.requireScenario(); if (points.length % 2 !== 0) throw new RangeError("points must contain x/y pairs"); const velocity = cellVelocity(this.u, this.v, dimensions(scenario.domain).nx, dimensions(scenario.domain).ny, scenario.precision); const output = allocate(scenario.precision, points.length); for (let i = 0; i < points.length; i += 2) { const sampled = sampleCell(velocity, scenario.domain, points[i] ?? 0, points[i + 1] ?? 0); output[i] = sampled[0]; output[i + 1] = sampled[1]; } return output; }
  public exportState(): CanonicalFlowState { const scenario = this.requireScenario(); const {nx, ny} = dimensions(scenario.domain); return {schemaVersion: 1, dimension: 2, bounds: scenario.domain.bounds, resolution: scenario.domain.resolution, periodicAxes: scenario.domain.periodicAxes, time: this.time, precision: scenario.precision, angleDegrees: this.control.angleDegrees, angularVelocityDegrees: this.control.angularVelocityDegrees, sourceLanguage: "typescript", sourceSolver: this.info.id, velocity: cellVelocity(this.u, this.v, nx, ny, scenario.precision), density: null}; }
  public importState(state: CanonicalFlowState, control: ControlState): ImportOutcome { const scenario = this.requireScenario(); if (state.dimension !== 2 || state.resolution[0] !== scenario.domain.resolution[0] || state.resolution[1] !== scenario.domain.resolution[1]) return {status: "rejected", reason: "incompatible_domain", discardedState: [], warnings: []}; if (!state.velocity.every(Number.isFinite)) return {status: "rejected", reason: "nonfinite_state", discardedState: [], warnings: []}; const {nx, ny, dx, dy} = dimensions(scenario.domain); ({u: this.u, v: this.v} = cellToFaces(state.velocity, nx, ny, scenario.precision)); this.time = state.time; this.control = control; this.updateSolid(control); project(this.u, this.v, this.solid, nx, ny, dx, dy, scenario.precision, scenario.solverOptions.pressureMaxIterations ?? 640, scenario.solverOptions.pressureTolerance ?? 1e-5); return {status: "accepted", reason: "none", discardedState: ["pressure history"], warnings: []}; }
  public diagnostics(): Diagnostics { const scenario = this.requireScenario(); const {nx, ny, dx, dy} = dimensions(scenario.domain); const velocity = cellVelocity(this.u, this.v, nx, ny, scenario.precision); const div = divergence(this.u, this.v, nx, ny, dx, dy, scenario.precision); let energy = 0; let maxDivergence = 0; for (let i = 0; i < nx * ny; i += 1) { energy += 0.5 * ((velocity[2 * i] ?? 0) ** 2 + (velocity[2 * i + 1] ?? 0) ** 2); maxDivergence = Math.max(maxDivergence, Math.abs(div[i] ?? 0)); } return {values: {energy: energy / (nx * ny), divergence_linf: maxDivergence, effective_reynolds: this.reynolds}, warnings: []}; }
}
