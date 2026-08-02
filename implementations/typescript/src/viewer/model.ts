import type {ControlState, FlowSolver, Scenario, SolverId, StepReport} from "../core/contracts.js";
import {NumericalFailure} from "../core/contracts.js";
import {NacaFoil} from "../core/geometry.js";
import {bounds2d, dimensions} from "../core/grid.js";
import {controlAt} from "../core/scenario.js";
import {createSolver} from "../solvers/factory.js";
import type {ViewerSnapshot} from "./protocol.js";
import {TracerSystem} from "./tracers.js";

interface PresentationState {
  vorticityVisible: boolean;
  cropEnabled: boolean;
  status: string;
  recoveryEpoch: number;
  poseOnly: boolean;
}

export class ViewerModel {
  public solver: FlowSolver;
  public readonly tracers: TracerSystem;
  public readonly presentation: PresentationState;
  public time = 0;
  public paused = false;
  public appliedCommand = 0;
  public revision = 0;
  private manualAngle: number | null = null;
  private angularVelocity = 0;
  private dragging = false;
  private poseSamples: {time: number; angle: number}[] = [];
  private stepRate: number | null = null;
  private simulatedPerWall: number | null = null;
  private lastReport: StepReport = {requestedDt: 0, advancedDt: 0, substeps: 0, maxSpeed: 0, warnings: []};
  private failureTimes: number[] = [];
  private diagnosticsReady = false;
  private vorticityCache = new Float32Array();
  private nextVorticityTime = 0;

  public constructor(public readonly scenario: Scenario, solverId: SolverId) {
    this.solver = createSolver(solverId);
    this.solver.initialize(scenario, scenario.seed);
    this.tracers = new TracerSystem(scenario);
    this.presentation = {
      vorticityVisible: true,
      cropEnabled: scenario.solverOptions.viewerCropDefault ?? false,
      status: "warming",
      recoveryEpoch: 0,
      poseOnly: false,
    };
  }

  public get vorticityVisible(): boolean { return this.presentation.vorticityVisible; }
  public set vorticityVisible(value: boolean) { this.presentation.vorticityVisible = value; this.invalidateVorticity(); }
  public get cropEnabled(): boolean { return this.presentation.cropEnabled; }
  public set cropEnabled(value: boolean) { this.presentation.cropEnabled = value; }
  public get status(): string { return this.presentation.status; }
  public set status(value: string) { this.presentation.status = value; }

  public control(nextTime: number): ControlState {
    const scheduled = controlAt(this.scenario, nextTime);
    if (this.manualAngle === null) return scheduled;
    return {time: nextTime, angleDegrees: this.manualAngle, angularVelocityDegrees: this.presentation.poseOnly ? 0 : this.angularVelocity};
  }

  public setAngle(angle: number, timestamp: number): void {
    const selected = Math.max(-30, Math.min(30, angle));
    this.dragging = true;
    this.poseSamples.push({time: timestamp, angle: selected});
    const cutoff = timestamp - 80;
    while (this.poseSamples.length > 2 && (this.poseSamples[1]?.time ?? timestamp) < cutoff) this.poseSamples.shift();
    const first = this.poseSamples[0];
    if (first !== undefined && timestamp > first.time) {
      const reference = Math.max(Math.hypot(this.scenario.freestream[0] ?? 0, this.scenario.freestream[1] ?? 0), 1e-6);
      const maximum = 8 * reference / this.scenario.foil.chord * 180 / Math.PI;
      this.angularVelocity = Math.max(-maximum, Math.min(maximum, (selected - first.angle) / ((timestamp - first.time) / 1000)));
    }
    this.manualAngle = selected;
    this.status = "manual control";
  }

  public releaseAngle(): void {
    this.dragging = false;
    this.angularVelocity = 0;
    this.poseSamples = [];
  }

  public setReynolds(reynolds: number): void {
    this.solver.setReynolds(reynolds);
    this.status = "Re changed; warming";
    this.clearMeasurements();
  }

  public toggleVorticity(): void { this.vorticityVisible = !this.vorticityVisible; }
  public toggleCrop(): void { if ((this.scenario.solverOptions.viewerCropCells ?? 0) > 0) this.cropEnabled = !this.cropEnabled; }

  public reset(): void {
    const id = this.solver.info.id;
    this.solver = createSolver(id);
    this.solver.initialize(this.scenario, this.scenario.seed);
    this.time = 0;
    this.manualAngle = null;
    this.angularVelocity = 0;
    this.dragging = false;
    this.poseSamples = [];
    this.presentation.poseOnly = false;
    this.failureTimes = [];
    this.tracers.reseed();
    this.status = "warming";
    this.clearMeasurements();
  }

  public switchSolver(id: SolverId): boolean {
    if (id === this.solver.info.id) return true;
    const incoming = createSolver(id);
    incoming.initialize(this.scenario, this.scenario.seed);
    incoming.setReynolds(this.solver.reynolds);
    const importedAt = this.control(this.time);
    const outcome = incoming.importState(this.solver.exportState(), importedAt);
    if (outcome.status === "rejected") {
      this.status = `switch rejected: ${outcome.reason}; source retained`;
      return false;
    }
    const validationDt = this.scenario.outputDt;
    let report: StepReport;
    try {
      report = incoming.advance(this.control(this.time + validationDt), validationDt);
    } catch (error) {
      if (error instanceof NumericalFailure) {
        this.status = `switch rejected: ${error.reason}; source retained`;
        return false;
      }
      throw error;
    }
    this.solver = incoming;
    this.time += report.advancedDt;
    this.lastReport = report;
    this.status = `switched; discarded ${outcome.discardedState.join(", ")}`;
    this.clearMeasurements();
    this.diagnosticsReady = true;
    return true;
  }

  public step(): void {
    if (this.paused) return;
    const dt = this.scenario.outputDt;
    const started = performance.now();
    let report: StepReport;
    try {
      report = this.solver.advance(this.control(this.time + dt), dt);
    } catch (error) {
      if (error instanceof NumericalFailure) this.recover(error);
      else { this.paused = true; this.status = `unexpected ${error instanceof Error ? error.name : "failure"}; paused`; }
      return;
    }
    this.lastReport = report;
    this.time += report.advancedDt;
    this.diagnosticsReady = true;
    if (this.presentation.poseOnly && !this.dragging) {
      this.presentation.poseOnly = false;
      this.status = "motion resolved; running";
    } else this.status = "running";
    try {
      const displayScale = Math.min(1.5, Math.max(0.5, 1 + 0.5 * Math.log10(this.solver.reynolds / Math.max(this.scenario.reynolds, 1))));
      this.tracers.advance(this.solver, dt, displayScale);
    } catch (error) {
      this.status = `presentation failure: ${error instanceof Error ? error.name : "unknown"}; flow retained`;
    }
    const elapsed = Math.max((performance.now() - started) / 1000, 1e-9);
    const instantRate = 1 / elapsed; const instantThroughput = dt / elapsed;
    this.stepRate = this.stepRate === null ? instantRate : 0.85 * this.stepRate + 0.15 * instantRate;
    this.simulatedPerWall = this.simulatedPerWall === null ? instantThroughput : 0.85 * this.simulatedPerWall + 0.15 * instantThroughput;
    const stableCutoff = performance.now() - 3000;
    this.failureTimes = this.failureTimes.filter((value) => value >= stableCutoff);
  }

  private recover(error: NumericalFailure): void {
    const now = performance.now();
    this.failureTimes = this.failureTimes.filter((value) => value >= now - 3000);
    this.failureTimes.push(now);
    const movingRapidly = this.dragging && Math.abs(this.angularVelocity) > 30;
    if (movingRapidly && this.failureTimes.length >= 2) this.presentation.poseOnly = true;
    let resetReynolds = false;
    if (this.failureTimes.length >= 3) {
      if (Math.abs(this.solver.reynolds - this.scenario.reynolds) > 1e-9) resetReynolds = true;
      else if (!movingRapidly || this.presentation.poseOnly) {
        this.paused = true;
        this.status = `repeated ${error.reason} at baseline Re; paused`;
        return;
      }
    }
    const requestedReynolds = resetReynolds ? this.scenario.reynolds : this.solver.reynolds;
    const angle = this.control(this.time).angleDegrees;
    const replacement = createSolver(this.solver.info.id);
    try {
      const freshScenario: Scenario = {...this.scenario, controls: [{time: 0, angleDegrees: angle}]};
      replacement.initialize(freshScenario, this.scenario.seed);
      replacement.setReynolds(requestedReynolds);
      const fresh = replacement.exportState();
      const outcome = replacement.importState({...fresh, time: this.time, angleDegrees: angle, angularVelocityDegrees: 0}, {time: this.time, angleDegrees: angle, angularVelocityDegrees: 0});
      if (outcome.status === "rejected") throw new NumericalFailure("projection_failure", `fresh state import rejected: ${outcome.reason}`);
      this.solver = replacement;
      this.manualAngle = angle;
      this.angularVelocity = 0;
      this.poseSamples = [];
      this.presentation.recoveryEpoch += 1;
      this.tracers.reseed();
      this.status = `recovered=${String(this.presentation.recoveryEpoch)} reason=${error.reason} discarded=solver-private${resetReynolds ? " Re=reset" : ""}${this.presentation.poseOnly ? " motion=pose-only" : ""}`;
      this.clearMeasurements();
    } catch (recoveryError) {
      this.paused = true;
      this.status = `recovery failed after ${error.reason}: ${recoveryError instanceof Error ? recoveryError.name : "unknown"}; paused`;
    }
  }

  private clearMeasurements(): void {
    this.stepRate = null;
    this.simulatedPerWall = null;
    this.diagnosticsReady = false;
    this.lastReport = {requestedDt: 0, advancedDt: 0, substeps: 0, maxSpeed: 0, warnings: []};
    this.invalidateVorticity();
  }

  private invalidateVorticity(): void { this.vorticityCache = new Float32Array(); this.nextVorticityTime = this.time; }

  private vorticity(): Float32Array {
    if (!this.vorticityVisible) return new Float32Array();
    if (this.vorticityCache.length > 0 && this.time < this.nextVorticityTime) return this.vorticityCache.slice();
    const {nx, ny, dx, dy} = dimensions(this.scenario.domain);
    const velocity = this.solver.exportState().velocity;
    const output = new Float32Array(nx * ny);
    for (let y = 1; y + 1 < ny; y += 1) for (let x = 1; x + 1 < nx; x += 1) {
      const dvdx = ((velocity[2 * (y * nx + x + 1) + 1] ?? 0) - (velocity[2 * (y * nx + x - 1) + 1] ?? 0)) / (2 * dx);
      const dudy = ((velocity[2 * ((y + 1) * nx + x)] ?? 0) - (velocity[2 * ((y - 1) * nx + x)] ?? 0)) / (2 * dy);
      output[y * nx + x] = dvdx - dudy;
    }
    this.vorticityCache = output;
    this.nextVorticityTime = this.time + 0.1;
    return output.slice();
  }

  public snapshot(): ViewerSnapshot {
    const {nx, ny} = dimensions(this.scenario.domain);
    const bounds = bounds2d(this.scenario.domain);
    const angle = this.control(this.time).angleDegrees;
    const diagnostics = this.diagnosticsReady ? this.solver.diagnostics().values : {};
    this.revision += 1;
    return {
      kind: "snapshot", revision: this.revision, appliedCommand: this.appliedCommand,
      solverId: this.solver.info.id, time: this.time, angleDegrees: angle, reynolds: this.solver.reynolds,
      paused: this.paused, vorticityVisible: this.vorticityVisible, cropEnabled: this.cropEnabled, tracerMode: this.tracers.mode,
      stepRate: this.stepRate, simulatedPerWall: this.simulatedPerWall, substeps: this.lastReport.substeps,
      maxSpeed: this.lastReport.maxSpeed, diagnostics, status: this.status,
      recoveryEpoch: this.presentation.recoveryEpoch, poseOnly: this.presentation.poseOnly, scheduleActive: this.manualAngle === null,
      resolution: [nx, ny], bounds: [bounds.x, bounds.y], tracerPositions: this.tracers.positions.slice(),
      pathSegments: this.tracers.segments(), vorticity: this.vorticity(), foilOutline: new NacaFoil(this.scenario.foil).outline(angle),
    };
  }
}
