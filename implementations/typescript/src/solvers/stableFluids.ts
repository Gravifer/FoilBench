import type {CanonicalFlowState, ControlState, Diagnostics, FlowSolver, FloatArray, ImportOutcome, InteractiveTuning, InteractiveTuningValue, RestartState, ReynoldsOutcome, Scenario, SolverInfo, StepReport} from "../core/contracts.js";
import {NumericalFailure} from "../core/contracts.js";
import {NacaFoil} from "../core/geometry.js";
import {allocate, bounds2d, cellToFaces, cellVelocity, dimensions, divergence, project, sampleCell} from "../core/grid.js";
import type {ProjectionReport} from "../core/grid.js";
import {fieldDiagnostics} from "../core/metrics.js";
import {acceptedImport, rejectedImport} from "../core/outcomes.js";
import {requireFiniteControl, validateCanonicalState} from "../core/stateValidation.js";

type TransportMode = "maccormack" | "semi-lagrangian" | "skew-rk2";

export interface StableCheckpoint {
  readonly u: FloatArray;
  readonly v: FloatArray;
  readonly solid: Uint8Array;
  readonly time: number;
  readonly control: ControlState;
  readonly stateRevision: number;
  readonly projection: ProjectionReport;
  readonly viscosity: IterativeReport;
}

interface IterativeReport {readonly criterion: "update-linf"; readonly tolerance: number; readonly iterations: number; readonly finalResidual: number; readonly converged: boolean}

const emptyProjection = (): ProjectionReport => ({criterion: "relative-residual-l2", tolerance: 0, iterations: 0, finalResidual: 0, relativeResidual: 0, divergenceLinf: 0, converged: true});
const emptyIteration = (): IterativeReport => ({criterion: "update-linf", tolerance: 0, iterations: 0, finalResidual: 0, converged: true});

export class StableFluidsSolver implements FlowSolver {
  public readonly info: SolverInfo = {id: "stable-fluids", displayName: "Stable Fluids (MAC)", dimensions: [2], supportsMovingBoundary: true, supportedPrecisions: ["float32", "float64"], acceleration: "typed-arrays"};
  public reynolds = 1;
  public transportMode: TransportMode = "maccormack";
  private scenario: Scenario | null = null;
  private foil: NacaFoil | null = null;
  private u: FloatArray = new Float32Array();
  private v: FloatArray = new Float32Array();
  private solid = new Uint8Array();
  private rollback: StableCheckpoint | null = null;
  private time = 0;
  private revision = 0;
  private lastProjection: ProjectionReport = emptyProjection();
  private lastViscosity: IterativeReport = emptyIteration();
  private control: ControlState = {time: 0, angleDegrees: 0, angularVelocityDegrees: 0};
  public get stateRevision(): number { return this.revision; }

  public initialize(scenario: Scenario, seed: number): void {
    this.restart(scenario, seed, {time: 0, angleDegrees: scenario.controls[0]?.angleDegrees ?? 0, reynolds: scenario.reynolds});
  }

  public restart(scenario: Scenario, seed: number, start: RestartState): void {
    void seed;
    if (scenario.domain.dimension !== 2) throw new RangeError("stable-fluids supports only 2D");
    if (!Number.isFinite(start.time) || start.time < 0 || !Number.isFinite(start.angleDegrees) || !Number.isFinite(start.reynolds) || start.reynolds <= 0) throw new RangeError("invalid stable-fluids restart state");
    this.scenario = scenario; this.foil = new NacaFoil(scenario.foil); this.reynolds = start.reynolds; this.time = start.time;
    this.rollback = null;
    this.transportMode = scenario.solverOptions.stableAdvection ?? "maccormack";
    this.control = {time: start.time, angleDegrees: start.angleDegrees, angularVelocityDegrees: 0};
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
    this.lastProjection = project(this.u, this.v, this.solid, nx, ny, dx, dy, scenario.precision, scenario.solverOptions.pressureMaxIterations ?? 640, scenario.solverOptions.pressureTolerance ?? 1e-5, periodicX, periodicY); this.requireProjection(this.lastProjection);
    this.revision = 0;
  }

  public setReynolds(reynolds: number): ReynoldsOutcome { if (!(reynolds > 0) || !Number.isFinite(reynolds)) throw new RangeError("Reynolds must be finite and positive"); if (reynolds !== this.reynolds) { this.reynolds = reynolds; this.revision += 1; } return {requested: reynolds, effective: this.reynolds, warnings: []}; }
  public setTransportMode(mode: TransportMode): void { if (mode !== this.transportMode) { this.transportMode = mode; this.revision += 1; } }
  public interactiveTuning(): InteractiveTuning {
    return {id: "stable-advection", label: "adv", value: this.transportMode, canDecrease: this.transportMode !== "maccormack", canIncrease: this.transportMode !== "skew-rk2"};
  }
  public adjustInteractiveTuning(direction: -1 | 1): InteractiveTuning {
    this.setTransportMode(direction < 0 ? "maccormack" : "skew-rk2");
    return this.interactiveTuning();
  }
  public applyInteractiveTuning(value: InteractiveTuningValue): InteractiveTuning {
    if (value !== "maccormack" && value !== "semi-lagrangian" && value !== "skew-rk2") throw new RangeError("invalid stable-advection tuning");
    this.setTransportMode(value);
    return this.interactiveTuning();
  }
  private requireScenario(): Scenario { if (this.scenario === null) throw new Error("solver is not initialized"); return this.scenario; }

  private updateSolid(control: ControlState): void {
    const scenario = this.requireScenario(); const foil = this.foil; if (foil === null) throw new Error("foil is missing");
    const {nx, ny, dx, dy} = dimensions(scenario.domain); const {x: bx, y: by} = bounds2d(scenario.domain);
    for (let y = 0; y < ny; y += 1) for (let x = 0; x < nx; x += 1) this.solid[y * nx + x] = foil.signedDistance(bx[0] + (x + 0.5) * dx, by[0] + (y + 0.5) * dy, control.angleDegrees) <= 0 ? 1 : 0;
  }

  private enforceSolidFacesOn(u: FloatArray, v: FloatArray, control: ControlState): void {
    const scenario = this.requireScenario(); const {nx, ny, dx, dy} = dimensions(scenario.domain); const {x: bx, y: by} = bounds2d(scenario.domain); const pivotX = scenario.foil.pivot[0] ?? 0; const pivotY = scenario.foil.pivot[1] ?? 0; const omega = control.angularVelocityDegrees * Math.PI / 180;
    for (let y = 0; y < ny; y += 1) for (let x = 0; x <= nx; x += 1) { const leftSolid = x > 0 && this.solid[y * nx + x - 1] !== 0; const rightSolid = x < nx && this.solid[y * nx + x] !== 0; if (leftSolid || rightSolid) { const py = by[0] + (y + 0.5) * dy; u[y * (nx + 1) + x] = -omega * (py - pivotY); } }
    for (let y = 0; y <= ny; y += 1) for (let x = 0; x < nx; x += 1) { const bottomSolid = y > 0 && this.solid[(y - 1) * nx + x] !== 0; const topSolid = y < ny && this.solid[y * nx + x] !== 0; if (bottomSolid || topSolid) { const px = bx[0] + (x + 0.5) * dx; v[y * nx + x] = omega * (px - pivotX); } }
  }

  private enforceSolidFaces(control: ControlState): void { this.enforceSolidFacesOn(this.u, this.v, control); }

  private applyDomainBoundaries(u: FloatArray, v: FloatArray): void {
    const scenario = this.requireScenario(); const {nx, ny} = dimensions(scenario.domain); const ux = scenario.freestream[0] ?? 0; const uy = scenario.freestream[1] ?? 0; const periodicX = scenario.domain.periodicAxes.includes("x"); const periodicY = scenario.domain.periodicAxes.includes("y");
    if (periodicX) for (let y = 0; y < ny; y += 1) { const average = 0.5 * ((u[y * (nx + 1)] ?? 0) + (u[y * (nx + 1) + nx] ?? 0)); u[y * (nx + 1)] = average; u[y * (nx + 1) + nx] = average; }
    else for (let y = 0; y < ny; y += 1) { u[y * (nx + 1)] = ux; u[y * (nx + 1) + nx] = u[y * (nx + 1) + nx - 1] ?? ux; v[y * nx] = uy; v[y * nx + nx - 1] = v[y * nx + Math.max(0, nx - 2)] ?? uy; }
    if (periodicY) for (let x = 0; x < nx; x += 1) { const average = 0.5 * ((v[x] ?? 0) + (v[ny * nx + x] ?? 0)); v[x] = average; v[ny * nx + x] = average; }
    else for (let x = 0; x < nx; x += 1) { v[x] = uy; v[ny * nx + x] = uy; }
  }

  private solidFaceLeakage(control: ControlState): number {
    const scenario = this.requireScenario(); const {nx, ny, dx, dy} = dimensions(scenario.domain); const {x: bx, y: by} = bounds2d(scenario.domain); const pivotX = scenario.foil.pivot[0] ?? 0; const pivotY = scenario.foil.pivot[1] ?? 0; const omega = control.angularVelocityDegrees * Math.PI / 180; let maximum = 0;
    for (let y = 0; y < ny; y += 1) for (let x = 1; x < nx; x += 1) { const leftSolid = this.solid[y * nx + x - 1] !== 0; const rightSolid = this.solid[y * nx + x] !== 0; if (leftSolid === rightSolid) continue; const py = by[0] + (y + 0.5) * dy; const wallU = -omega * (py - pivotY); maximum = Math.max(maximum, Math.abs((this.u[y * (nx + 1) + x] ?? 0) - wallU)); }
    for (let y = 1; y < ny; y += 1) for (let x = 0; x < nx; x += 1) { const bottomSolid = this.solid[(y - 1) * nx + x] !== 0; const topSolid = this.solid[y * nx + x] !== 0; if (bottomSolid === topSolid) continue; const px = bx[0] + (x + 0.5) * dx; const wallV = omega * (px - pivotX); maximum = Math.max(maximum, Math.abs((this.v[y * nx + x] ?? 0) - wallV)); }
    return maximum;
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

  private skewConvectionFaces(u: FloatArray, v: FloatArray): {u: FloatArray; v: FloatArray} {
    const scenario = this.requireScenario(); const {nx, ny, dx, dy} = dimensions(scenario.domain); const periodicX = scenario.domain.periodicAxes.includes("x"); const periodicY = scenario.domain.periodicAxes.includes("y"); const outputU = allocate(scenario.precision, u.length); const outputV = allocate(scenario.precision, v.length);
    const derivative = (field: FloatArray, width: number, height: number, x: number, y: number, axis: "x" | "y", spacing: number, periodic: boolean, duplicateEndpoint = false): number => {
      if (axis === "x") {
        const logicalWidth = width - (periodic && duplicateEndpoint ? 1 : 0); const logicalX = x >= logicalWidth ? 0 : x;
        const left = periodic ? (logicalX - 1 + logicalWidth) % logicalWidth : Math.max(0, x - 1); const right = periodic ? (logicalX + 1) % logicalWidth : Math.min(width - 1, x + 1);
        if (!periodic && x === 0) return (-3 * (field[y * width] ?? 0) + 4 * (field[y * width + 1] ?? 0) - (field[y * width + Math.min(2, width - 1)] ?? 0)) / (2 * spacing);
        if (!periodic && x === width - 1) return (3 * (field[y * width + x] ?? 0) - 4 * (field[y * width + x - 1] ?? 0) + (field[y * width + Math.max(0, x - 2)] ?? 0)) / (2 * spacing);
        return ((field[y * width + right] ?? 0) - (field[y * width + left] ?? 0)) / (2 * spacing);
      }
      const logicalHeight = height - (periodic && duplicateEndpoint ? 1 : 0); const logicalY = y >= logicalHeight ? 0 : y;
      const bottom = periodic ? (logicalY - 1 + logicalHeight) % logicalHeight : Math.max(0, y - 1); const top = periodic ? (logicalY + 1) % logicalHeight : Math.min(height - 1, y + 1);
      if (!periodic && y === 0) return (-3 * (field[x] ?? 0) + 4 * (field[width + x] ?? 0) - (field[Math.min(2, height - 1) * width + x] ?? 0)) / (2 * spacing);
      if (!periodic && y === height - 1) return (3 * (field[y * width + x] ?? 0) - 4 * (field[(y - 1) * width + x] ?? 0) + (field[Math.max(0, y - 2) * width + x] ?? 0)) / (2 * spacing);
      return ((field[top * width + x] ?? 0) - (field[bottom * width + x] ?? 0)) / (2 * spacing);
    };
    const cellU = allocate(scenario.precision, nx * ny); const cellV = allocate(scenario.precision, nx * ny); for (let y = 0; y < ny; y += 1) for (let x = 0; x < nx; x += 1) { cellU[y * nx + x] = 0.5 * ((u[y * (nx + 1) + x] ?? 0) + (u[y * (nx + 1) + x + 1] ?? 0)); cellV[y * nx + x] = 0.5 * ((v[y * nx + x] ?? 0) + (v[(y + 1) * nx + x] ?? 0)); }
    const vOnU = allocate(scenario.precision, u.length); const uOnV = allocate(scenario.precision, v.length);
    for (let y = 0; y < ny; y += 1) for (let x = 0; x <= nx; x += 1) vOnU[y * (nx + 1) + x] = (periodicX && (x === 0 || x === nx)) ? 0.5 * ((cellV[y * nx + nx - 1] ?? 0) + (cellV[y * nx] ?? 0)) : x === 0 ? cellV[y * nx] ?? 0 : x === nx ? cellV[y * nx + nx - 1] ?? 0 : 0.5 * ((cellV[y * nx + x - 1] ?? 0) + (cellV[y * nx + x] ?? 0));
    for (let y = 0; y <= ny; y += 1) for (let x = 0; x < nx; x += 1) uOnV[y * nx + x] = (periodicY && (y === 0 || y === ny)) ? 0.5 * ((cellU[(ny - 1) * nx + x] ?? 0) + (cellU[x] ?? 0)) : y === 0 ? cellU[x] ?? 0 : y === ny ? cellU[(ny - 1) * nx + x] ?? 0 : 0.5 * ((cellU[(y - 1) * nx + x] ?? 0) + (cellU[y * nx + x] ?? 0));
    const uu = allocate(scenario.precision, u.length); const vu = allocate(scenario.precision, u.length); const uv = allocate(scenario.precision, v.length); const vv = allocate(scenario.precision, v.length); for (let index = 0; index < u.length; index += 1) { uu[index] = (u[index] ?? 0) ** 2; vu[index] = (vOnU[index] ?? 0) * (u[index] ?? 0); } for (let index = 0; index < v.length; index += 1) { uv[index] = (uOnV[index] ?? 0) * (v[index] ?? 0); vv[index] = (v[index] ?? 0) ** 2; }
    for (let y = 0; y < ny; y += 1) for (let x = 0; x <= nx; x += 1) { const index = y * (nx + 1) + x; const advective = (u[index] ?? 0) * derivative(u, nx + 1, ny, x, y, "x", dx, periodicX, periodicX) + (vOnU[index] ?? 0) * derivative(u, nx + 1, ny, x, y, "y", dy, periodicY); const conservative = derivative(uu, nx + 1, ny, x, y, "x", dx, periodicX, periodicX) + derivative(vu, nx + 1, ny, x, y, "y", dy, periodicY); outputU[index] = 0.5 * (advective + conservative); }
    for (let y = 0; y <= ny; y += 1) for (let x = 0; x < nx; x += 1) { const index = y * nx + x; const advective = (uOnV[index] ?? 0) * derivative(v, nx, ny + 1, x, y, "x", dx, periodicX) + (v[index] ?? 0) * derivative(v, nx, ny + 1, x, y, "y", dy, periodicY, periodicY); const conservative = derivative(uv, nx, ny + 1, x, y, "x", dx, periodicX) + derivative(vv, nx, ny + 1, x, y, "y", dy, periodicY, periodicY); outputV[index] = 0.5 * (advective + conservative); }
    return {u: outputU, v: outputV};
  }

  private skewRk2Faces(control: ControlState, dt: number): {u: FloatArray; v: FloatArray} {
    const scenario = this.requireScenario(); const first = this.skewConvectionFaces(this.u, this.v); const midpointU = allocate(scenario.precision, this.u.length); const midpointV = allocate(scenario.precision, this.v.length); for (let index = 0; index < this.u.length; index += 1) midpointU[index] = (this.u[index] ?? 0) - 0.5 * dt * (first.u[index] ?? 0); for (let index = 0; index < this.v.length; index += 1) midpointV[index] = (this.v[index] ?? 0) - 0.5 * dt * (first.v[index] ?? 0); this.applyDomainBoundaries(midpointU, midpointV); this.enforceSolidFacesOn(midpointU, midpointV, control); const second = this.skewConvectionFaces(midpointU, midpointV); const outputU = allocate(scenario.precision, this.u.length); const outputV = allocate(scenario.precision, this.v.length); for (let index = 0; index < this.u.length; index += 1) outputU[index] = (this.u[index] ?? 0) - dt * (second.u[index] ?? 0); for (let index = 0; index < this.v.length; index += 1) outputV[index] = (this.v[index] ?? 0) - dt * (second.v[index] ?? 0); this.applyDomainBoundaries(outputU, outputV); this.enforceSolidFacesOn(outputU, outputV, control); return {u: outputU, v: outputV};
  }

  private skewFaceAdvectionRate(u: FloatArray, v: FloatArray): number {
    const scenario = this.requireScenario(); const {nx, ny, dx, dy} = dimensions(scenario.domain);
    const cellU = allocate(scenario.precision, nx * ny); const cellV = allocate(scenario.precision, nx * ny);
    for (let y = 0; y < ny; y += 1) for (let x = 0; x < nx; x += 1) { cellU[y * nx + x] = 0.5 * ((u[y * (nx + 1) + x] ?? 0) + (u[y * (nx + 1) + x + 1] ?? 0)); cellV[y * nx + x] = 0.5 * ((v[y * nx + x] ?? 0) + (v[(y + 1) * nx + x] ?? 0)); }
    let selected = 0;
    for (let y = 0; y < ny; y += 1) for (let x = 0; x <= nx; x += 1) { const cross = x === 0 ? cellV[y * nx] ?? 0 : x === nx ? cellV[y * nx + nx - 1] ?? 0 : 0.5 * ((cellV[y * nx + x - 1] ?? 0) + (cellV[y * nx + x] ?? 0)); selected = Math.max(selected, Math.abs(u[y * (nx + 1) + x] ?? 0) / dx + Math.abs(cross) / dy); }
    for (let y = 0; y <= ny; y += 1) for (let x = 0; x < nx; x += 1) { const cross = y === 0 ? cellU[x] ?? 0 : y === ny ? cellU[(ny - 1) * nx + x] ?? 0 : 0.5 * ((cellU[(y - 1) * nx + x] ?? 0) + (cellU[y * nx + x] ?? 0)); selected = Math.max(selected, Math.abs(cross) / dx + Math.abs(v[y * nx + x] ?? 0) / dy); }
    return selected;
  }

  private diffuse(velocity: FloatArray, dt: number): FloatArray {
    const scenario = this.requireScenario(); const {nx, ny, dx, dy} = dimensions(scenario.domain); const speed = Math.max(Math.hypot(scenario.freestream[0] ?? 0, scenario.freestream[1] ?? 0), 1); const viscosity = speed * scenario.foil.chord / this.reynolds; const ax = viscosity * dt / (dx * dx); const ay = viscosity * dt / (dy * dy); if (ax + ay < 1e-8) { this.lastViscosity = emptyIteration(); return velocity; } const periodicX = scenario.domain.periodicAxes.includes("x"); const periodicY = scenario.domain.periodicAxes.includes("y"); let current: FloatArray = allocate(scenario.precision, velocity.length); current.set(velocity); let next: FloatArray = allocate(scenario.precision, velocity.length); const sample = (field: FloatArray, x: number, y: number, component: number): number => { const sx = periodicX ? (x + nx) % nx : Math.max(0, Math.min(nx - 1, x)); const sy = periodicY ? (y + ny) % ny : Math.max(0, Math.min(ny - 1, y)); return field[2 * (sy * nx + sx) + component] ?? 0; };
    const tolerance = scenario.solverOptions.pressureTolerance ?? 1e-5; let converged = false; let performed = 0; let finalResidual = Number.POSITIVE_INFINITY;
    for (let iteration = 0; iteration < 80; iteration += 1) { let maxChange = 0; for (let y = 0; y < ny; y += 1) for (let x = 0; x < nx; x += 1) for (let component = 0; component < 2; component += 1) { const index = 2 * (y * nx + x) + component; const value = ((velocity[index] ?? 0) + ax * (sample(current, x - 1, y, component) + sample(current, x + 1, y, component)) + ay * (sample(current, x, y - 1, component) + sample(current, x, y + 1, component))) / (1 + 2 * ax + 2 * ay); maxChange = Math.max(maxChange, Math.abs(value - (current[index] ?? 0))); next[index] = value; } const swap = current; current = next; next = swap; performed = iteration + 1; finalResidual = maxChange; if (maxChange < tolerance) { converged = true; break; } }
    this.lastViscosity = {criterion: "update-linf", tolerance, iterations: performed, finalResidual, converged}; if (!converged) throw new NumericalFailure("convergence_failure", "implicit viscosity solve did not converge", "viscosity", {iterations: performed, tolerance, final_residual: finalResidual});
    return current;
  }

  private diffuseFaces(u: FloatArray, v: FloatArray, dt: number): {u: FloatArray; v: FloatArray} {
    const scenario = this.requireScenario(); const {nx, ny, dx, dy} = dimensions(scenario.domain); const speed = Math.max(Math.hypot(scenario.freestream[0] ?? 0, scenario.freestream[1] ?? 0), 1); const viscosity = speed * scenario.foil.chord / this.reynolds; const ax = viscosity * dt / (dx * dx); const ay = viscosity * dt / (dy * dy); if (ax + ay < 1e-8) { this.lastViscosity = emptyIteration(); return {u: u.slice(), v: v.slice()}; }
    const periodicX = scenario.domain.periodicAxes.includes("x"); const periodicY = scenario.domain.periodicAxes.includes("y"); const tolerance = scenario.solverOptions.pressureTolerance ?? 1e-5; const maximumIterations = scenario.solverOptions.pressureMaxIterations ?? 640;
    const solve = (field: FloatArray, width: number, height: number): {field: FloatArray; report: IterativeReport} => {
      const duplicateX = periodicX && width === nx + 1; const duplicateY = periodicY && height === ny + 1; const logicalWidth = width - (duplicateX ? 1 : 0); const logicalHeight = height - (duplicateY ? 1 : 0); const original = allocate(scenario.precision, logicalWidth * logicalHeight); for (let y = 0; y < logicalHeight; y += 1) for (let x = 0; x < logicalWidth; x += 1) original[y * logicalWidth + x] = field[y * width + x] ?? 0; let current = original.slice() as FloatArray; let next = allocate(scenario.precision, current.length); let scale = 1; for (const value of original) scale = Math.max(scale, Math.abs(value)); let performed = 0; let finalResidual = Number.POSITIVE_INFINITY;
      const sample = (candidate: FloatArray, x: number, y: number): number => { const sx = periodicX ? (x + logicalWidth) % logicalWidth : Math.max(0, Math.min(logicalWidth - 1, x)); const sy = periodicY ? (y + logicalHeight) % logicalHeight : Math.max(0, Math.min(logicalHeight - 1, y)); return candidate[sy * logicalWidth + sx] ?? 0; };
      for (let iteration = 0; iteration < maximumIterations; iteration += 1) { let maximumChange = 0; for (let y = 0; y < logicalHeight; y += 1) for (let x = 0; x < logicalWidth; x += 1) { const index = y * logicalWidth + x; const value = ((original[index] ?? 0) + ax * (sample(current, x - 1, y) + sample(current, x + 1, y)) + ay * (sample(current, x, y - 1) + sample(current, x, y + 1))) / (1 + 2 * ax + 2 * ay); maximumChange = Math.max(maximumChange, Math.abs(value - (current[index] ?? 0))); next[index] = value; } const swap = current; current = next; next = swap; performed = iteration + 1; finalResidual = maximumChange / scale; if (finalResidual <= tolerance) break; }
      const output = allocate(scenario.precision, field.length); for (let y = 0; y < logicalHeight; y += 1) for (let x = 0; x < logicalWidth; x += 1) output[y * width + x] = current[y * logicalWidth + x] ?? 0; if (duplicateX) for (let y = 0; y < height; y += 1) output[y * width + width - 1] = output[y * width] ?? 0; if (duplicateY) for (let x = 0; x < width; x += 1) output[(height - 1) * width + x] = output[x] ?? 0; return {field: output, report: {criterion: "update-linf", tolerance, iterations: performed, finalResidual, converged: finalResidual <= tolerance}};
    };
    const selectedU = solve(u, nx + 1, ny); const selectedV = solve(v, nx, ny + 1); this.lastViscosity = {criterion: "update-linf", tolerance, iterations: Math.max(selectedU.report.iterations, selectedV.report.iterations), finalResidual: Math.max(selectedU.report.finalResidual, selectedV.report.finalResidual), converged: selectedU.report.converged && selectedV.report.converged}; if (!this.lastViscosity.converged) throw new NumericalFailure("convergence_failure", "implicit face-viscosity solve did not converge", "viscosity", {iterations: this.lastViscosity.iterations, tolerance, final_residual: this.lastViscosity.finalResidual}); return {u: selectedU.field, v: selectedV.field};
  }

  private requireProjection(report: ProjectionReport): void { if (!report.converged) throw new NumericalFailure("projection_failure", "pressure projection did not meet its convergence criterion", "projection", {iterations: report.iterations, tolerance: report.tolerance, final_residual: report.finalResidual, relative_residual: report.relativeResidual, divergence_linf: report.divergenceLinf}); }

  private refreshFinalDivergence(): void { const scenario = this.requireScenario(); const {nx, ny, dx, dy} = dimensions(scenario.domain); const field = divergence(this.u, this.v, nx, ny, dx, dy, scenario.precision); let maximum = 0; for (let index = 0; index < field.length; index += 1) if (this.solid[index] === 0) maximum = Math.max(maximum, Math.abs(field[index] ?? 0)); this.lastProjection = {...this.lastProjection, divergenceLinf: maximum}; }

  private requireMacPostconditions(control: ControlState): Readonly<Record<string, number>> {
    const scenario = this.requireScenario();
    const divergenceLimit = scenario.solverOptions.macMaximumDivergenceLinf ?? Number.POSITIVE_INFINITY;
    const leakageLimit = scenario.solverOptions.macMaximumSolidLeakage ?? Number.POSITIVE_INFINITY;
    if (!(divergenceLimit >= 0) || !(leakageLimit >= 0) || Number.isNaN(divergenceLimit) || Number.isNaN(leakageLimit)) throw new RangeError("MAC postcondition limits must be non-negative numbers");
    const divergenceLinf = this.lastProjection.divergenceLinf;
    const solidLeakage = this.solidFaceLeakage(control);
    if (divergenceLinf > divergenceLimit || solidLeakage > leakageLimit) throw new NumericalFailure("postcondition_failure", "Stable Fluids exceeded a configured MAC postcondition limit", "postcondition", {divergence_linf: divergenceLinf, maximum_divergence_linf: divergenceLimit, solid_leakage: solidLeakage, maximum_solid_leakage: leakageLimit});
    return {divergence_linf: divergenceLinf, solid_leakage: solidLeakage};
  }

  private substep(control: ControlState, dt: number): void {
    const scenario = this.requireScenario(); const {nx, ny, dx, dy} = dimensions(scenario.domain); const periodicX = scenario.domain.periodicAxes.includes("x"); const periodicY = scenario.domain.periodicAxes.includes("y"); if (this.transportMode === "skew-rk2") { ({u: this.u, v: this.v} = this.skewRk2Faces(control, dt)); ({u: this.u, v: this.v} = this.diffuseFaces(this.u, this.v, dt)); } else { const before = cellVelocity(this.u, this.v, nx, ny, scenario.precision); const advected = this.transportMode === "semi-lagrangian" ? this.semiLagrangian(before, dt) : this.maccormack(before, dt); const diffused = this.diffuse(advected, dt); ({u: this.u, v: this.v} = cellToFaces(diffused, nx, ny, scenario.precision, periodicX, periodicY)); } this.updateSolid(control); this.applyDomainBoundaries(this.u, this.v); this.enforceSolidFaces(control); this.lastProjection = project(this.u, this.v, this.solid, nx, ny, dx, dy, scenario.precision, scenario.solverOptions.pressureMaxIterations ?? 640, scenario.solverOptions.pressureTolerance ?? 1e-5, periodicX, periodicY); this.requireProjection(this.lastProjection); this.applyDomainBoundaries(this.u, this.v); this.enforceSolidFaces(control); this.refreshFinalDivergence();
  }

  private projectedSubstep(control: ControlState, dt: number): void {
    const scenario = this.requireScenario(); const {nx, ny, dx, dy} = dimensions(scenario.domain);
    const diffused = this.diffuse(cellVelocity(this.u, this.v, nx, ny, scenario.precision), dt); const periodicX = scenario.domain.periodicAxes.includes("x"); const periodicY = scenario.domain.periodicAxes.includes("y");
    ({u: this.u, v: this.v} = cellToFaces(diffused, nx, ny, scenario.precision, periodicX, periodicY)); this.updateSolid(control); this.enforceSolidFaces(control);
    if (!periodicX) { const inlet = scenario.freestream[0] ?? 0; for (let y = 0; y < ny; y += 1) this.u[y * (nx + 1)] = inlet; }
    this.lastProjection = project(this.u, this.v, this.solid, nx, ny, dx, dy, scenario.precision, scenario.solverOptions.pressureMaxIterations ?? 640, scenario.solverOptions.pressureTolerance ?? 1e-5, periodicX, periodicY); this.requireProjection(this.lastProjection); this.enforceSolidFaces(control); this.refreshFinalDivergence();
  }

  public checkpoint(destination?: StableCheckpoint): StableCheckpoint {
    const u = destination?.u.length === this.u.length ? destination.u : this.u.slice();
    const v = destination?.v.length === this.v.length ? destination.v : this.v.slice();
    const solid = destination?.solid.length === this.solid.length ? destination.solid : this.solid.slice();
    if (destination !== undefined) { u.set(this.u); v.set(this.v); solid.set(this.solid); }
    return {u, v, solid, time: this.time, control: this.control, stateRevision: this.revision, projection: this.lastProjection, viscosity: this.lastViscosity};
  }
  public restore(checkpoint: StableCheckpoint): void {
    if (this.u.length === checkpoint.u.length) this.u.set(checkpoint.u); else this.u = checkpoint.u.slice();
    if (this.v.length === checkpoint.v.length) this.v.set(checkpoint.v); else this.v = checkpoint.v.slice();
    if (this.solid.length === checkpoint.solid.length) this.solid.set(checkpoint.solid); else this.solid = checkpoint.solid.slice();
    this.time = checkpoint.time; this.control = checkpoint.control; this.revision = checkpoint.stateRevision; this.lastProjection = checkpoint.projection; this.lastViscosity = checkpoint.viscosity;
  }
  private transactionCheckpoint(): StableCheckpoint { const saved = this.checkpoint(this.rollback ?? undefined); this.rollback = saved; return saved; }

  public importFaces(u: FloatArray, v: FloatArray, time: number, control: ControlState): ImportOutcome {
    const scenario = this.requireScenario(); const {nx, ny, dx, dy} = dimensions(scenario.domain);
    if (u.length !== ny * (nx + 1) || v.length !== (ny + 1) * nx) return rejectedImport("incompatible_domain", "particle-transfer");
    if (!u.every(Number.isFinite) || !v.every(Number.isFinite) || !Number.isFinite(time) || time < 0) return rejectedImport("nonfinite_state", "particle-transfer");
    const saved = this.transactionCheckpoint(); const periodicX = scenario.domain.periodicAxes.includes("x"); const periodicY = scenario.domain.periodicAxes.includes("y");
    try {
      this.u = allocate(scenario.precision, u.length); this.u.set(u); this.v = allocate(scenario.precision, v.length); this.v.set(v); this.solid = new Uint8Array(nx * ny); this.time = time; this.control = control;
      this.updateSolid(control); this.enforceSolidFaces(control); this.lastProjection = project(this.u, this.v, this.solid, nx, ny, dx, dy, scenario.precision, scenario.solverOptions.pressureMaxIterations ?? 640, scenario.solverOptions.pressureTolerance ?? 1e-5, periodicX, periodicY); this.requireProjection(this.lastProjection); this.enforceSolidFaces(control); this.refreshFinalDivergence();
      if (!this.u.every(Number.isFinite) || !this.v.every(Number.isFinite)) { this.restore(saved); return rejectedImport("projection_failure", "projection"); }
    } catch (error) { this.restore(saved); if (error instanceof NumericalFailure) return rejectedImport(error.reason, error.stage, error.evidence); throw error; }
    this.revision += 1;
    return acceptedImport(["pressure history"]);
  }

  public stageParticleFaces(u: FloatArray, v: FloatArray): void {
    const scenario = this.requireScenario(); const {nx, ny} = dimensions(scenario.domain);
    if (u.length !== ny * (nx + 1) || v.length !== (ny + 1) * nx) throw new NumericalFailure("transfer_failure", "particle face transfer has the wrong shape", "particle-transfer");
    if (!u.every(Number.isFinite) || !v.every(Number.isFinite)) throw new NumericalFailure("nonfinite_state", "particle face transfer is non-finite", "particle-transfer");
    this.u = u; this.v = v; this.applyDomainBoundaries(this.u, this.v); this.enforceSolidFaces(this.control);
  }

  public advanceProjected(control: ControlState, targetDt: number): StepReport {
    if (!(targetDt > 0) || !Number.isFinite(targetDt)) throw new RangeError("target dt must be finite and positive"); this.requireCompletionTime(control, targetDt); const saved = this.transactionCheckpoint();
    let postconditions: Readonly<Record<string, number>>;
    try { this.projectedSubstep(control, targetDt); if (!this.u.every(Number.isFinite) || !this.v.every(Number.isFinite)) throw new NumericalFailure("nonfinite_state", "projected grid step produced non-finite velocity"); postconditions = this.requireMacPostconditions(control); }
    catch (error) { this.restore(saved); throw error; }
    const velocity = cellVelocity(this.u, this.v, dimensions(this.requireScenario().domain).nx, dimensions(this.requireScenario().domain).ny, this.requireScenario().precision); let maxSpeed = 0; for (let index = 0; index < velocity.length; index += 2) maxSpeed = Math.max(maxSpeed, Math.hypot(velocity[index] ?? 0, velocity[index + 1] ?? 0)); const evidence = {...this.motionEvidence(maxSpeed, targetDt, control, control.angleDegrees - saved.control.angleDegrees), ...postconditions};
    this.time += targetDt; this.control = {...control, time: this.time}; this.revision += 1;
    return {requestedDt: targetDt, advancedDt: targetDt, substeps: 1, maxSpeed, stateRevision: this.revision, evidence, warnings: []};
  }

  private requireCompletionTime(control: ControlState, targetDt: number): void {
    requireFiniteControl(control);
    const expected = this.time + targetDt; const tolerance = this.requireScenario().precision === "float32" ? 1e-6 : 1e-12;
    if (!Number.isFinite(control.time) || Math.abs(control.time - expected) > tolerance * Math.max(1, Math.abs(expected))) throw new NumericalFailure("time_contract_failure", "control completion time disagrees with target interval", "time-mapping", {expected_time: expected, control_time: control.time, target_dt: targetDt});
  }

  private motionEvidence(maxSpeed: number, substepDt: number, control: ControlState, angleDeltaDegrees: number): Readonly<Record<string, number | boolean>> {
    const scenario = this.requireScenario(); const {dx, dy} = dimensions(scenario.domain); const omega = Math.abs(control.angularVelocityDegrees) * Math.PI / 180; const maximumWallSpeed = omega * scenario.foil.chord; const fluidCfl = substepDt * maxSpeed * (1 / dx + 1 / dy); const displacement = substepDt * maxSpeed / Math.min(dx, dy); const boundarySweep = scenario.foil.chord * Math.abs(angleDeltaDegrees) * Math.PI / (180 * Math.min(dx, dy));
    return {maximum_fluid_speed: maxSpeed, maximum_wall_speed: maximumWallSpeed, maximum_cfl: fluidCfl, maximum_characteristic_displacement: displacement, maximum_boundary_sweep: boundarySweep, pressure_converged: this.lastProjection.converged, pressure_iterations: this.lastProjection.iterations, pressure_relative_residual: this.lastProjection.relativeResidual, divergence_linf: this.lastProjection.divergenceLinf, solid_leakage: this.solidFaceLeakage(control), viscosity_converged: this.lastViscosity.converged, viscosity_iterations: this.lastViscosity.iterations, viscosity_final_residual: this.lastViscosity.finalResidual, requested_reynolds: this.reynolds, effective_reynolds: this.reynolds, degraded_motion: maximumWallSpeed === 0 && Math.abs(angleDeltaDegrees) > 1e-9};
  }

  public advance(control: ControlState, targetDt: number): StepReport {
    if (!(targetDt > 0) || !Number.isFinite(targetDt)) throw new RangeError("target dt must be finite and positive");
    this.requireCompletionTime(control, targetDt);
    const scenario = this.requireScenario();
    const {nx, ny, dx, dy} = dimensions(scenario.domain);
    const velocity = cellVelocity(this.u, this.v, nx, ny, scenario.precision);
    let maxSpeed = 0;
    for (let index = 0; index < velocity.length; index += 2) maxSpeed = Math.max(maxSpeed, Math.hypot(velocity[index] ?? 0, velocity[index + 1] ?? 0));
    const configuredCfl = scenario.solverOptions.stableCfl ?? 0.7;
    const cfl = this.transportMode === "skew-rk2" ? Math.min(configuredCfl, 0.4) : configuredCfl;
    const angleDelta = control.angleDegrees - this.control.angleDegrees;
    const wallSpeed = Math.abs(control.angularVelocityDegrees) * Math.PI / 180 * scenario.foil.chord;
    const spacing = Math.min(dx, dy);
    const sweepCells = scenario.foil.chord * Math.abs(angleDelta) * Math.PI / (180 * spacing);
    let fluidRate = this.transportMode === "skew-rk2" ? this.skewFaceAdvectionRate(this.u, this.v) : maxSpeed / spacing;
    const required = Math.max(targetDt * fluidRate / cfl, targetDt * wallSpeed / (cfl * spacing), sweepCells / cfl);
    let substeps = Math.max(1, Math.ceil(required));
    if (substeps > 512) throw new NumericalFailure("stability_limit", "stable-fluids motion requires too many internal substeps", this.transportMode === "skew-rk2" ? "advection" : "boundary", {required_substeps: substeps, maximum_substeps: 512, maximum_fluid_speed: maxSpeed, maximum_wall_speed: wallSpeed, boundary_sweep_cells: sweepCells});
    let dt = targetDt / substeps;
    const saved = this.transactionCheckpoint();
    let acceptedMeasure = Number.POSITIVE_INFINITY;
    let stabilityRetries = 0;
    for (;;) {
      try {
        for (let step = 0; step < substeps; step += 1) {
          const fraction = (step + 1) / substeps;
          this.substep({time: this.time + fraction * targetDt, angleDegrees: this.control.angleDegrees + fraction * angleDelta, angularVelocityDegrees: control.angularVelocityDegrees}, dt);
        }
        if (!this.u.every(Number.isFinite) || !this.v.every(Number.isFinite)) throw new NumericalFailure("nonfinite_state", "stable-fluids produced non-finite velocity", "postcondition");
        const updated = cellVelocity(this.u, this.v, nx, ny, scenario.precision);
        maxSpeed = 0;
        for (let index = 0; index < updated.length; index += 2) maxSpeed = Math.max(maxSpeed, Math.hypot(updated[index] ?? 0, updated[index + 1] ?? 0));
        fluidRate = this.transportMode === "skew-rk2" ? this.skewFaceAdvectionRate(this.u, this.v) : maxSpeed / spacing;
        acceptedMeasure = dt * fluidRate;
      } catch (error) {
        this.restore(saved);
        throw error;
      }
      if (acceptedMeasure <= cfl * (1 + 1e-6)) break;
      const nextSubsteps = Math.max(substeps + 1, Math.ceil(1.05 * substeps * acceptedMeasure / cfl));
      this.restore(saved);
      if (nextSubsteps > 512) throw new NumericalFailure("stability_limit", "stable-fluids retry requires too many internal substeps", "advection", {accepted_measure: acceptedMeasure, maximum_measure: cfl, required_substeps: nextSubsteps, maximum_substeps: 512, stability_retries: stabilityRetries});
      stabilityRetries += 1;
      substeps = nextSubsteps;
      dt = targetDt / substeps;
    }
    let postconditions: Readonly<Record<string, number>>;
    try { postconditions = this.requireMacPostconditions(control); }
    catch (error) { this.restore(saved); throw error; }
    const evidence = {...this.motionEvidence(maxSpeed, dt, control, angleDelta / substeps), ...postconditions, maximum_advective_rate: fluidRate, stability_retries: stabilityRetries};
    this.time += targetDt;
    this.control = {...control, time: this.time};
    this.revision += 1;
    return {requestedDt: targetDt, advancedDt: targetDt, substeps, maxSpeed, stateRevision: this.revision, evidence, warnings: []};
  }

  public sampleVelocity(points: FloatArray): FloatArray { const scenario = this.requireScenario(); if (points.length % 2 !== 0) throw new RangeError("points must contain x/y pairs"); const velocity = cellVelocity(this.u, this.v, dimensions(scenario.domain).nx, dimensions(scenario.domain).ny, scenario.precision); const output = allocate(scenario.precision, points.length); for (let index = 0; index < points.length; index += 2) { const sampled = sampleCell(velocity, scenario.domain, points[index] ?? 0, points[index + 1] ?? 0); output[index] = sampled[0]; output[index + 1] = sampled[1]; } return output; }
  public exportState(): CanonicalFlowState { const scenario = this.requireScenario(); const {nx, ny} = dimensions(scenario.domain); const velocity = cellVelocity(this.u, this.v, nx, ny, scenario.precision); for (let cell = 0; cell < this.solid.length; cell += 1) if (this.solid[cell] !== 0) { velocity[2 * cell] = 0; velocity[2 * cell + 1] = 0; } return {schemaVersion: 1, dimension: 2, bounds: scenario.domain.bounds, resolution: scenario.domain.resolution, periodicAxes: scenario.domain.periodicAxes, time: this.time, precision: scenario.precision, angleDegrees: this.control.angleDegrees, angularVelocityDegrees: this.control.angularVelocityDegrees, sourceLanguage: "typescript", sourceSolver: this.info.id, velocity, density: null}; }
  public importState(state: CanonicalFlowState, control: ControlState): ImportOutcome {
    const scenario = this.requireScenario();
    const invalid = validateCanonicalState(state, scenario, control);
    if (invalid !== null) return invalid;
    if (Math.abs(state.time - control.time) > (scenario.precision === "float32" ? 1e-6 : 1e-12) * Math.max(1, Math.abs(state.time))) return rejectedImport("time_contract_failure", "canonical-import", {state_time: state.time, control_time: control.time});
    const saved = this.transactionCheckpoint();
    const {nx, ny, dx, dy} = dimensions(scenario.domain); const periodicX = scenario.domain.periodicAxes.includes("x"); const periodicY = scenario.domain.periodicAxes.includes("y");
    try {
      ({u: this.u, v: this.v} = cellToFaces(state.velocity, nx, ny, scenario.precision, periodicX, periodicY));
      this.solid = new Uint8Array(nx * ny); this.time = state.time; this.control = control;
      this.updateSolid(control); this.applyDomainBoundaries(this.u, this.v); this.enforceSolidFaces(control);
      this.lastProjection = project(this.u, this.v, this.solid, nx, ny, dx, dy, scenario.precision, scenario.solverOptions.pressureMaxIterations ?? 640, scenario.solverOptions.pressureTolerance ?? 1e-5, periodicX, periodicY); this.requireProjection(this.lastProjection);
      this.applyDomainBoundaries(this.u, this.v); this.enforceSolidFaces(control); this.refreshFinalDivergence();
      if (!this.u.every(Number.isFinite) || !this.v.every(Number.isFinite)) {
        this.restore(saved);
        return rejectedImport("projection_failure", "projection");
      }
    } catch (error) {
      this.restore(saved);
      if (error instanceof NumericalFailure) return rejectedImport(error.reason, error.stage, error.evidence);
      throw error;
    }
    this.revision += 1;
    return acceptedImport(["pressure history"]);
  }
  public diagnostics(): Diagnostics { const scenario = this.requireScenario(); const foil = this.foil; if (foil === null) throw new Error("foil is missing"); const {nx, ny} = dimensions(scenario.domain); const velocity = cellVelocity(this.u, this.v, nx, ny, scenario.precision); return {stateRevision: this.revision, values: {...fieldDiagnostics(velocity, scenario, foil, this.control.angleDegrees), divergence_linf: this.lastProjection.divergenceLinf, solid_leakage: this.solidFaceLeakage(this.control), effective_reynolds: this.reynolds}, warnings: []}; }
}
