import type {ControlState, FlowSolver, Scenario, SolverId, StepReport} from "../core/contracts.js";
import {NumericalFailure} from "../core/contracts.js";
import {NacaFoil} from "../core/geometry.js";
import {bounds2d, dimensions} from "../core/grid.js";
import {controlAt} from "../core/scenario.js";
import {createSolver} from "../solvers/factory.js";
import {PicFlipSolver} from "../solvers/picFlip.js";
import {StableFluidsSolver} from "../solvers/stableFluids.js";
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
  public playbackRate = 1;
  private manualAngle: number | null = null;
  private angularVelocity = 0;
  private dragging = false;
  private poseSamples: {time: number; angle: number}[] = [];
  private stepRate: number | null = null;
  private simulatedPerWall: number | null = null;
  private lastReport: StepReport = {requestedDt: 0, advancedDt: 0, substeps: 0, maxSpeed: 0, warnings: []};
  private failureTimes: number[] = [];
  private diagnosticsReady = false;
  private diagnosticsCache: Readonly<Record<string, number>> = {};
  private nextDiagnosticsTime = 0;
  private vorticityCache = new Float32Array();
  private nextVorticityTime = 0;
  private stableTransport: "maccormack" | "semi-lagrangian" | "skew-rk2";
  private picBlend: number;

  public constructor(public readonly scenario: Scenario, solverId: SolverId) {
    this.solver = createSolver(solverId);
    this.solver.initialize(scenario, scenario.seed);
    this.stableTransport = scenario.solverOptions.stableAdvection ?? "maccormack";
    this.picBlend = scenario.solverOptions.picFlipBlend ?? 0.95;
    this.configureSolver(this.solver);
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
    const selected = Math.max(50, Math.min(100_000, reynolds));
    this.solver.setReynolds(selected);
    this.playbackRate = Math.max(0.5, Math.min(2, (selected / this.scenario.reynolds) ** Math.log10(1.5)));
    this.status = "Re changed; warming";
    this.clearMeasurements();
  }

  public toggleVorticity(): void { this.vorticityVisible = !this.vorticityVisible; }
  public toggleCrop(): void { if ((this.scenario.solverOptions.viewerCropCells ?? 0) > 0) this.cropEnabled = !this.cropEnabled; }
  public adjustSolverTuning(amount: -1 | 1): void {
    if (this.solver instanceof StableFluidsSolver) {
      this.stableTransport = amount < 0 ? "maccormack" : "skew-rk2";
      this.solver.setTransportMode(this.stableTransport);
      this.status = `transport=${this.stableTransport}`;
    } else if (this.solver instanceof PicFlipSolver) {
      this.picBlend = Math.max(0, Math.min(1, this.picBlend + 0.05 * amount));
      this.solver.setBlend(this.picBlend);
      this.status = `FLIP=${this.picBlend.toFixed(2)}`;
    } else this.status = "no live tuning for LBM";
  }

  private configureSolver(solver: FlowSolver): void {
    if (solver instanceof StableFluidsSolver) solver.setTransportMode(this.stableTransport);
    if (solver instanceof PicFlipSolver) solver.setBlend(this.picBlend);
  }

  public reset(): void {
    const id = this.solver.info.id;
    this.solver = createSolver(id);
    this.solver.initialize(this.scenario, this.scenario.seed);
    this.stableTransport = this.scenario.solverOptions.stableAdvection ?? "maccormack";
    this.picBlend = this.scenario.solverOptions.picFlipBlend ?? 0.95;
    this.configureSolver(this.solver);
    this.time = 0;
    this.manualAngle = null;
    this.angularVelocity = 0;
    this.dragging = false;
    this.poseSamples = [];
    this.presentation.poseOnly = false;
    this.playbackRate = 1;
    this.failureTimes = [];
    this.tracers.reseed();
    this.status = "warming";
    this.clearMeasurements();
  }

  public switchSolver(id: SolverId): boolean {
    if (id === this.solver.info.id) return true;
    const incoming = createSolver(id);
    incoming.initialize(this.scenario, this.scenario.seed);
    this.configureSolver(incoming);
    incoming.setReynolds(this.solver.reynolds);
    const importedAt = this.control(this.time);
    const outcome = incoming.importState(this.solver.exportState(), importedAt);
    if (outcome.status === "rejected") {
      this.status = `switch rejected: ${outcome.reason}; source retained`;
      return false;
    }
    const validationDt = this.scenario.outputDt * this.playbackRate;
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

  public step(): number {
    if (this.paused) return 0;
    const dt = this.scenario.outputDt * this.playbackRate;
    let report: StepReport;
    try {
      report = this.solver.advance(this.control(this.time + dt), dt);
    } catch (error) {
      if (error instanceof NumericalFailure) this.recover(error);
      else { this.paused = true; this.status = `unexpected ${error instanceof Error ? error.name : "failure"}; paused`; }
      return 0;
    }
    this.lastReport = report;
    this.time += report.advancedDt;
    this.diagnosticsReady = true;
    if (this.presentation.poseOnly && !this.dragging) {
      this.presentation.poseOnly = false;
      this.status = "motion resolved; running";
    } else this.status = "running";
    try {
      this.tracers.advance(this.solver, dt, 1);
    } catch (error) {
      this.status = `presentation failure: ${error instanceof Error ? error.name : "unknown"}; flow retained`;
    }
    const stableCutoff = performance.now() - 3000;
    this.failureTimes = this.failureTimes.filter((value) => value >= stableCutoff);
    return report.advancedDt;
  }

  public recordOwnerCycle(advancedDt: number, elapsedSeconds: number): void {
    if (!(advancedDt > 0) || !(elapsedSeconds > 0) || !Number.isFinite(elapsedSeconds)) return;
    const instantRate = 1 / elapsedSeconds;
    const instantThroughput = advancedDt / elapsedSeconds;
    this.stepRate = this.stepRate === null ? instantRate : 0.85 * this.stepRate + 0.15 * instantRate;
    this.simulatedPerWall = this.simulatedPerWall === null ? instantThroughput : 0.85 * this.simulatedPerWall + 0.15 * instantThroughput;
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
      this.configureSolver(replacement);
      replacement.setReynolds(requestedReynolds);
      const fresh = replacement.exportState();
      const outcome = replacement.importState({...fresh, time: this.time, angleDegrees: angle, angularVelocityDegrees: 0}, {time: this.time, angleDegrees: angle, angularVelocityDegrees: 0});
      if (outcome.status === "rejected") throw new NumericalFailure("projection_failure", `fresh state import rejected: ${outcome.reason}`);
      this.solver = replacement;
      if (resetReynolds) this.playbackRate = 1;
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
    this.diagnosticsCache = {};
    this.nextDiagnosticsTime = this.time;
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

  private diagnostics(): Readonly<Record<string, number>> {
    if (!this.diagnosticsReady) return {};
    if (Object.keys(this.diagnosticsCache).length > 0 && this.time < this.nextDiagnosticsTime) return this.diagnosticsCache;
    try {
      this.diagnosticsCache = this.solver.diagnostics().values;
      this.nextDiagnosticsTime = this.time + 0.1;
    } catch (error) {
      this.status = `diagnostic failure: ${error instanceof Error ? error.name : "unknown"}; flow retained`;
    }
    return this.diagnosticsCache;
  }

  public snapshot(): ViewerSnapshot {
    const {nx, ny} = dimensions(this.scenario.domain);
    const bounds = bounds2d(this.scenario.domain);
    const angle = this.control(this.time).angleDegrees;
    const diagnostics = this.diagnostics();
    let vorticity: Float32Array = new Float32Array();
    try { vorticity = this.vorticity(); }
    catch (error) { this.status = `vorticity failure: ${error instanceof Error ? error.name : "unknown"}; flow retained`; }
    this.revision += 1;
    return {
      kind: "snapshot", revision: this.revision, appliedCommand: this.appliedCommand,
      solverId: this.solver.info.id, time: this.time, angleDegrees: angle, reynolds: this.solver.reynolds, playbackRate: this.playbackRate,
      paused: this.paused, vorticityVisible: this.vorticityVisible, cropEnabled: this.cropEnabled, tracerMode: this.tracers.mode,
      stepRate: this.stepRate, simulatedPerWall: this.simulatedPerWall, substeps: this.lastReport.substeps,
      maxSpeed: this.lastReport.maxSpeed, diagnostics, status: this.status,
      recoveryEpoch: this.presentation.recoveryEpoch, poseOnly: this.presentation.poseOnly, scheduleActive: this.manualAngle === null, solverTuning: this.solver instanceof StableFluidsSolver ? `adv=${this.stableTransport}` : this.solver instanceof PicFlipSolver ? `FLIP=${this.picBlend.toFixed(2)}` : "TRT",
      resolution: [nx, ny], bounds: [bounds.x, bounds.y], tracerPositions: this.tracers.positions.slice(),
      pathSegments: this.tracers.segments(), vorticity, foilOutline: new NacaFoil(this.scenario.foil).outline(angle),
    };
  }
}
