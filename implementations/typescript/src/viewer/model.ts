import type {ControlState, FlowSolver, ImportReason, InteractiveTuningValue, Scenario, SolverId, StepReport} from "../core/contracts.js";
import {NumericalFailure} from "../core/contracts.js";
import {NacaFoil} from "../core/geometry.js";
import {bounds2d, dimensions} from "../core/grid.js";
import {controlAt} from "../core/scenario.js";
import {createSolver} from "../solvers/factory.js";
import type {ViewerSnapshot} from "./protocol.js";
import {TracerSystem} from "./tracers.js";

const POSE_SAMPLE_WINDOW_MILLISECONDS = 80;
const POSE_ONLY_RELEASE_SPEED_RATIO = 0.5;
const POSE_ONLY_RELEASE_STEPS = 2;
const MAX_RESOLVED_TIP_SPEED_RATIO = 8;
const FAILURE_WINDOW_MILLISECONDS = 5000;
const FAILURE_LIMIT = 3;

interface PresentationState {
  vorticityVisible: boolean;
  cropEnabled: boolean;
  status: string;
  recoveryEpoch: number;
  recoveryReason: string | null;
  recoveryStage: string | null;
  poseOnly: boolean;
  diagnosticMode: "cadenced" | "every-step";
}

export interface ViewerSessionState {
  readonly phase: "warming" | "running" | "paused" | "failed";
  readonly motionMode: "resolved" | "pose-only";
  readonly diagnosticMode: "cadenced" | "every-step";
  readonly scheduleActive: boolean;
  readonly recoveryEpoch: number;
  readonly recoveryReason: string | null;
  readonly recoveryStage: string | null;
}

export interface ViewerSnapshotStorage {
  readonly tracerPositions: Float32Array;
  readonly pathSegments: Float32Array;
  readonly vorticity: Float32Array;
  readonly foilOutline: Float32Array;
}

export type SolverFactory = (id: SolverId) => FlowSolver;

export class ViewerModel {
  public solver: FlowSolver;
  public readonly tracers: TracerSystem;
  public readonly presentation: PresentationState;
  public time = 0;
  public paused = false;
  public appliedCommand = 0;
  public revision = 0;
  public solverEpoch = 0;
  public playbackRate = 1;
  private manualAngle: number | null = null;
  private angularVelocity = 0;
  private dragging = false;
  private poseSamples: {time: number; angle: number}[] = [];
  private lastPoseReceivedAt: number | null = null;
  private poseOnlyReleasePending = false;
  private poseOnlyCalmSteps = 0;
  private resolvedMotionTrial = false;
  private stepRate: number | null = null;
  private simulatedPerWall: number | null = null;
  private lastReport: StepReport = {requestedDt: 0, advancedDt: 0, substeps: 0, maxSpeed: 0, stateRevision: 0, evidence: {}, warnings: []};
  private failureTimes: number[] = [];
  private diagnosticsReady = false;
  private diagnosticsCache: Readonly<Record<string, number>> = {};
  private diagnosticsCacheRevision: number | null = null;
  private nextDiagnosticsTime = 0;
  private vorticityCache = new Float32Array();
  private vorticityCacheRevision: number | null = null;
  private nextVorticityTime = 0;
  private recoveryPending = false;
  private readonly tuningValues = new Map<SolverId, InteractiveTuningValue>();
  private readonly presentationFoil: NacaFoil;

  public constructor(public readonly scenario: Scenario, solverId: SolverId, private readonly solverFactory: SolverFactory = createSolver) {
    this.solver = this.solverFactory(solverId);
    this.solver.initialize(scenario, scenario.seed);
    this.rememberTuning(this.solver);
    this.tracers = new TracerSystem(scenario);
    this.presentationFoil = new NacaFoil(scenario.foil);
    this.presentation = {
      vorticityVisible: true,
      cropEnabled: (scenario.solverOptions.viewerCropCells ?? 0) > 0 && (scenario.solverOptions.viewerCropDefault ?? false),
      status: "warming",
      recoveryEpoch: 0,
      recoveryReason: null,
      recoveryStage: null,
      poseOnly: false,
      diagnosticMode: "cadenced",
    };
  }

  public get vorticityVisible(): boolean { return this.presentation.vorticityVisible; }
  public set vorticityVisible(value: boolean) { this.presentation.vorticityVisible = value; this.invalidateVorticity(); }
  public get cropEnabled(): boolean { return this.presentation.cropEnabled; }
  public set cropEnabled(value: boolean) { this.presentation.cropEnabled = (this.scenario.solverOptions.viewerCropCells ?? 0) > 0 && value; }
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
    this.lastPoseReceivedAt = performance.now();
    const previous = this.poseSamples.at(-1);
    if (previous !== undefined && (timestamp <= previous.time || timestamp - previous.time > POSE_SAMPLE_WINDOW_MILLISECONDS)) { this.poseSamples = []; this.angularVelocity = 0; }
    this.poseSamples.push({time: timestamp, angle: selected});
    const cutoff = timestamp - POSE_SAMPLE_WINDOW_MILLISECONDS;
    while (this.poseSamples.length > 2 && (this.poseSamples[1]?.time ?? timestamp) < cutoff) this.poseSamples.shift();
    const first = this.poseSamples[0];
    if (first !== undefined && timestamp > first.time) {
      const reference = Math.max(Math.hypot(this.scenario.freestream[0] ?? 0, this.scenario.freestream[1] ?? 0), 1e-6);
      const maximum = MAX_RESOLVED_TIP_SPEED_RATIO * reference / this.scenario.foil.chord * 180 / Math.PI;
      this.angularVelocity = Math.max(-maximum, Math.min(maximum, (selected - first.angle) / ((timestamp - first.time) / 1000)));
    }
    this.manualAngle = selected;
    this.status = "manual control";
  }

  public releaseAngle(): void {
    this.dragging = false;
    this.angularVelocity = 0;
    this.poseSamples = [];
    this.lastPoseReceivedAt = null;
    this.poseOnlyReleasePending = this.presentation.poseOnly;
  }

  public setReynolds(reynolds: number): void {
    const selected = Math.max(50, Math.min(100_000, reynolds));
    this.solver.setReynolds(selected);
    this.playbackRate = Math.max(0.5, Math.min(2, (selected / this.scenario.reynolds) ** Math.log10(1.5)));
    this.status = "Re changed; warming";
    this.recoveryPending = false;
    this.failureTimes = [];
    this.clearMeasurements();
  }

  public toggleVorticity(): void { this.vorticityVisible = !this.vorticityVisible; }
  public toggleCrop(): void { if ((this.scenario.solverOptions.viewerCropCells ?? 0) > 0) this.cropEnabled = !this.cropEnabled; }
  public toggleTracers(): void { this.tracers.toggleMode(); }
  public toggleDiagnostics(): void {
    this.presentation.diagnosticMode = this.presentation.diagnosticMode === "cadenced" ? "every-step" : "cadenced";
    this.nextDiagnosticsTime = this.time;
    this.nextVorticityTime = this.time;
  }
  public adjustSolverTuning(amount: -1 | 1): void {
    if (this.solver.adjustInteractiveTuning === undefined) { this.status = "no live tuning available"; return; }
    const tuning = this.solver.adjustInteractiveTuning(amount);
    this.tuningValues.set(this.solver.info.id, tuning.value);
    this.status = `${tuning.label}=${this.formatTuningValue(tuning.value)}`;
    this.clearMeasurements();
  }

  private rememberTuning(solver: FlowSolver): void {
    const tuning = solver.interactiveTuning?.();
    if (tuning !== undefined) this.tuningValues.set(solver.info.id, tuning.value);
  }

  private applySavedTuning(solver: FlowSolver): void {
    const saved = this.tuningValues.get(solver.info.id);
    if (saved !== undefined) solver.applyInteractiveTuning?.(saved);
    else this.rememberTuning(solver);
  }

  private formatTuningValue(value: InteractiveTuningValue): string {
    return typeof value === "number" ? value.toFixed(2) : value;
  }

  private tuningLabel(): string {
    const tuning = this.solver.interactiveTuning?.();
    return tuning === undefined ? "tuning=none" : `${tuning.label}=${this.formatTuningValue(tuning.value)}`;
  }

  public reset(): void {
    const id = this.solver.info.id;
    this.solver = this.solverFactory(id);
    this.solver.initialize(this.scenario, this.scenario.seed);
    this.solverEpoch += 1;
    this.tuningValues.clear();
    this.rememberTuning(this.solver);
    this.time = 0;
    this.paused = false;
    this.manualAngle = null;
    this.angularVelocity = 0;
    this.dragging = false;
    this.poseSamples = [];
    this.lastPoseReceivedAt = null;
    this.disablePoseOnly();
    this.presentation.recoveryReason = null;
    this.presentation.recoveryStage = null;
    this.playbackRate = 1;
    this.failureTimes = [];
    this.recoveryPending = false;
    this.tracers.reseed(this.scenario.controls[0]?.angleDegrees ?? 0, "scenario_reset");
    this.status = "warming";
    this.clearMeasurements();
  }

  public switchSolver(id: SolverId): boolean {
    if (id === this.solver.info.id) return true;
    const incoming = this.solverFactory(id);
    incoming.initialize(this.scenario, this.scenario.seed);
    this.applySavedTuning(incoming);
    incoming.setReynolds(this.solver.reynolds);
    const importedAt = this.control(this.time);
    const outcome = incoming.importState(this.solver.exportState(), importedAt);
    if (outcome.status === "rejected") {
      return this.rejectOrFallback(id, outcome.reason === "none" ? "unsupported_conversion" : outcome.reason);
    }
    const validationDt = this.scenario.outputDt * this.playbackRate;
    let report: StepReport;
    try {
      report = incoming.advance(this.control(this.time + validationDt), validationDt);
    } catch (error) {
      if (error instanceof NumericalFailure) {
        return this.rejectOrFallback(id, error.reason);
      }
      throw error;
    }
    this.solver = incoming;
    this.solverEpoch += 1;
    this.time += report.advancedDt;
    this.lastReport = report;
    this.status = `switched; discarded ${outcome.discardedState.join(", ")}`;
    try { this.tracers.advance(incoming, report.advancedDt); }
    catch (error) { this.status = `switched; presentation failure: ${error instanceof Error ? error.name : "unknown"}; flow retained`; }
    this.clearMeasurements();
    this.diagnosticsReady = true;
    return true;
  }

  private rejectOrFallback(id: SolverId, reason: Exclude<ImportReason, "none">): boolean {
    const transient = reason === "excessive_velocity" || reason === "stability_limit" || reason === "nonfinite_state" || reason === "convergence_failure" || reason === "projection_failure" || reason === "invalid_density" || reason === "invalid_population" || reason === "transfer_failure" || reason === "postcondition_failure";
    if (!transient) { this.status = `warm import rejected (${reason}); source retained`; return false; }
    const angle = this.control(this.time).angleDegrees;
    const incoming = this.solverFactory(id);
    try {
      incoming.restart(this.scenario, this.scenario.seed, {time: this.time, angleDegrees: angle, reynolds: this.solver.reynolds});
      this.applySavedTuning(incoming);
      const fresh = incoming.exportState();
      if (Math.abs(fresh.time - this.time) > 1e-9 || Math.abs(fresh.angleDegrees - angle) > 1e-9) throw new NumericalFailure("postcondition_failure", "fresh destination restart disagrees with requested time or pose");
    } catch (error) {
      const detail = error instanceof NumericalFailure ? error.reason : error instanceof Error ? error.name : "unknown";
      this.status = `warm import rejected (${reason}); fresh destination failed (${detail}); source retained`;
      return false;
    }
    this.solver = incoming;
    this.solverEpoch += 1;
    this.manualAngle = angle;
    this.angularVelocity = 0;
    this.poseSamples = [];
    this.presentation.recoveryEpoch += 1;
    this.presentation.recoveryReason = reason;
    this.presentation.recoveryStage = "warm-import-fallback";
    this.tracers.reseed(angle, "forced_recovery");
    this.status = `fresh destination reason=${reason}; stage=warm-import-fallback; private-state-discarded`;
    this.clearMeasurements();
    return true;
  }

  public step(): number {
    if (this.paused) return 0;
    this.settleIdleDrag(performance.now());
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
    const guardedTrial = this.resolvedMotionTrial;
    if (guardedTrial) this.resolvedMotionTrial = false;
    this.recoveryPending = false;
    this.status = guardedTrial ? "motion resolved; running" : "running";
    try {
      this.tracers.advance(this.solver, report.advancedDt);
    } catch (error) {
      this.status = `presentation failure: ${error instanceof Error ? error.name : "unknown"}; flow retained`;
    }
    if (this.presentation.poseOnly) {
      if (this.poseOnlyReleasePending && !this.dragging) this.disablePoseOnly(true);
      else if (this.requestedTipSpeedRatio() <= POSE_ONLY_RELEASE_SPEED_RATIO) {
        this.poseOnlyCalmSteps += 1;
        if (this.poseOnlyCalmSteps >= POSE_ONLY_RELEASE_STEPS) this.disablePoseOnly(true);
      } else this.poseOnlyCalmSteps = 0;
    }
    const stableCutoff = performance.now() - FAILURE_WINDOW_MILLISECONDS;
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
    if (this.resolvedMotionTrial || (this.presentation.poseOnly && this.poseOnlyReleasePending && !this.dragging)) {
      this.resolvedMotionTrial = false;
      this.paused = true;
      this.status = `failed resolved-motion trial (${error.reason}); paused`;
      return;
    }
    this.failureTimes = this.failureTimes.filter((value) => value >= now - FAILURE_WINDOW_MILLISECONDS);
    this.failureTimes.push(now);
    const movingRapidly = this.dragging && this.requestedTipSpeedRatio() > 1;
    const poseOnlyRecovery = this.recoveryPending && movingRapidly && !this.presentation.poseOnly;
    if (poseOnlyRecovery) this.enablePoseOnly();
    let resetReynolds = false;
    const reynoldsModified = Math.abs(this.solver.reynolds - this.scenario.reynolds) > 1e-9;
    if (!poseOnlyRecovery && reynoldsModified && (this.recoveryPending || this.failureTimes.length >= FAILURE_LIMIT)) resetReynolds = true;
    const baselineCircuitBreak = !reynoldsModified && this.failureTimes.length >= FAILURE_LIMIT;
    if (baselineCircuitBreak || (this.recoveryPending && !resetReynolds && !poseOnlyRecovery)) {
      this.paused = true;
      this.status = `repeated ${error.reason} at baseline Re; paused`;
      return;
    }
    const requestedReynolds = resetReynolds ? this.scenario.reynolds : this.solver.reynolds;
    const angle = this.control(this.time).angleDegrees;
    const replacement = this.solverFactory(this.solver.info.id);
    try {
      replacement.restart(this.scenario, this.scenario.seed, {time: this.time, angleDegrees: angle, reynolds: requestedReynolds});
      this.applySavedTuning(replacement);
      const fresh = replacement.exportState();
      if (Math.abs(fresh.time - this.time) > 1e-9 || Math.abs(fresh.angleDegrees - angle) > 1e-9) throw new NumericalFailure("postcondition_failure", "fresh recovery restart disagrees with requested time or pose");
      this.solver = replacement;
      this.solverEpoch += 1;
      if (resetReynolds) this.playbackRate = 1;
      this.manualAngle = angle;
      this.angularVelocity = 0;
      this.poseSamples = [];
      this.lastPoseReceivedAt = null;
      this.presentation.recoveryEpoch += 1;
      this.presentation.recoveryReason = error.reason;
      this.presentation.recoveryStage = "ordinary-step";
      this.tracers.reseed(angle, "forced_recovery");
      this.status = `recovered=${String(this.presentation.recoveryEpoch)} reason=${error.reason} stage=ordinary-step discarded=solver-private${resetReynolds ? " Re=reset" : ""}${this.presentation.poseOnly ? " motion=pose-only" : ""}`;
      this.clearMeasurements();
      this.recoveryPending = true;
      if (resetReynolds || poseOnlyRecovery) this.failureTimes = [];
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
    this.diagnosticsCacheRevision = null;
    this.nextDiagnosticsTime = this.time;
    this.lastReport = {requestedDt: 0, advancedDt: 0, substeps: 0, maxSpeed: 0, stateRevision: this.solver.stateRevision, evidence: {}, warnings: []};
    this.invalidateVorticity();
  }

  private invalidateVorticity(): void { this.vorticityCache = new Float32Array(); this.vorticityCacheRevision = null; this.nextVorticityTime = this.time; }

  private vorticity(): Float32Array {
    if (!this.vorticityVisible) return new Float32Array();
    if (this.presentation.diagnosticMode === "cadenced" && this.vorticityCache.length > 0 && this.time < this.nextVorticityTime) return this.vorticityCache;
    const {nx, ny, dx, dy} = dimensions(this.scenario.domain);
    const velocity = this.solver.exportState().velocity;
    const output = new Float32Array(nx * ny);
    for (let y = 1; y + 1 < ny; y += 1) for (let x = 1; x + 1 < nx; x += 1) {
      const dvdx = ((velocity[2 * (y * nx + x + 1) + 1] ?? 0) - (velocity[2 * (y * nx + x - 1) + 1] ?? 0)) / (2 * dx);
      const dudy = ((velocity[2 * ((y + 1) * nx + x)] ?? 0) - (velocity[2 * ((y - 1) * nx + x)] ?? 0)) / (2 * dy);
      output[y * nx + x] = dvdx - dudy;
    }
    this.vorticityCache = output;
    this.vorticityCacheRevision = this.solver.stateRevision;
    this.nextVorticityTime = this.time + 0.1;
    return output;
  }

  private diagnostics(): Readonly<Record<string, number>> {
    if (!this.diagnosticsReady) return {};
    if (this.presentation.diagnosticMode === "cadenced" && Object.keys(this.diagnosticsCache).length > 0 && this.time < this.nextDiagnosticsTime) return this.diagnosticsCache;
    try {
      const report = this.solver.diagnostics(); if (report.stateRevision !== this.solver.stateRevision) throw new Error("stale solver diagnostics revision"); this.diagnosticsCache = report.values; this.diagnosticsCacheRevision = report.stateRevision;
      this.nextDiagnosticsTime = this.time + 0.1;
    } catch (error) {
      this.status = `diagnostic failure: ${error instanceof Error ? error.name : "unknown"}; flow retained`;
    }
    return this.diagnosticsCache;
  }

  public sessionState(): ViewerSessionState {
    const failed = this.paused && (this.status.includes("failed") || this.status.includes("failure"));
    return {
      phase: failed ? "failed" : this.paused ? "paused" : this.diagnosticsReady ? "running" : "warming",
      motionMode: this.presentation.poseOnly ? "pose-only" : "resolved",
      diagnosticMode: this.presentation.diagnosticMode,
      scheduleActive: this.manualAngle === null,
      recoveryEpoch: this.presentation.recoveryEpoch,
      recoveryReason: this.presentation.recoveryReason,
      recoveryStage: this.presentation.recoveryStage,
    };
  }

  public createSnapshotStorage(): ViewerSnapshotStorage {
    const {nx, ny} = dimensions(this.scenario.domain);
    return {
      tracerPositions: new Float32Array(this.tracers.positions.length),
      pathSegments: new Float32Array(this.tracers.maximumSegmentScalars),
      vorticity: new Float32Array(nx * ny),
      foilOutline: new Float32Array(384),
    };
  }

  public snapshot(storage?: ViewerSnapshotStorage): ViewerSnapshot {
    const {nx, ny} = dimensions(this.scenario.domain);
    const bounds = bounds2d(this.scenario.domain);
    const angle = this.control(this.time).angleDegrees;
    const diagnostics = this.diagnostics();
    const session = this.sessionState();
    let vorticity: Float32Array = new Float32Array();
    try { vorticity = this.vorticity(); }
    catch (error) { this.status = `vorticity failure: ${error instanceof Error ? error.name : "unknown"}; flow retained`; }
    const tracerPositions = storage?.tracerPositions ?? new Float32Array(this.tracers.positions.length);
    tracerPositions.set(this.tracers.positions);
    const pathSegments = this.tracers.segments(storage?.pathSegments);
    const vorticityOutput = storage === undefined ? vorticity.slice() : storage.vorticity.subarray(0, vorticity.length);
    if (storage !== undefined) vorticityOutput.set(vorticity);
    const foilOutline = this.presentationFoil.outline(angle, 192, storage?.foilOutline);
    this.revision += 1;
    return {
      kind: "snapshot", revision: this.revision, appliedCommand: this.appliedCommand,
      solverEpoch: this.solverEpoch, solverStateRevision: this.solver.stateRevision,
      diagnosticSolverStateRevision: this.diagnosticsCacheRevision, vorticitySolverStateRevision: this.vorticityCacheRevision,
      solverId: this.solver.info.id, time: this.time, angleDegrees: angle, reynolds: this.solver.reynolds, playbackRate: this.playbackRate,
      paused: this.paused, vorticityVisible: this.vorticityVisible, cropEnabled: this.cropEnabled, tracerMode: this.tracers.mode,
      stepRate: this.stepRate, simulatedPerWall: this.simulatedPerWall, substeps: this.lastReport.substeps,
      maxSpeed: this.lastReport.maxSpeed, diagnostics, status: this.status,
      recoveryEpoch: session.recoveryEpoch, recoveryReason: session.recoveryReason, recoveryStage: session.recoveryStage,
      poseOnly: this.presentation.poseOnly, motionMode: session.motionMode, scheduleActive: session.scheduleActive,
      phase: session.phase, diagnosticMode: session.diagnosticMode, solverTuning: this.tuningLabel(),
      resolution: [nx, ny], bounds: [bounds.x, bounds.y], tracerPositions,
      pathSegments, vorticity: vorticityOutput, foilOutline,
    };
  }

  private settleIdleDrag(now: number): void {
    if (!this.dragging || this.lastPoseReceivedAt === null || now - this.lastPoseReceivedAt <= POSE_SAMPLE_WINDOW_MILLISECONDS) return;
    this.angularVelocity = 0;
    this.poseSamples = [];
    this.lastPoseReceivedAt = now;
  }

  private requestedTipSpeedRatio(): number {
    const reference = Math.max(Math.hypot(this.scenario.freestream[0] ?? 0, this.scenario.freestream[1] ?? 0), 1e-6);
    return Math.abs(this.angularVelocity) * Math.PI / 180 * this.scenario.foil.chord / reference;
  }

  private enablePoseOnly(): void {
    this.presentation.poseOnly = true;
    this.poseOnlyReleasePending = false;
    this.poseOnlyCalmSteps = 0;
    this.resolvedMotionTrial = false;
  }

  private disablePoseOnly(guardNextFailure = false): void {
    this.presentation.poseOnly = false;
    this.poseOnlyReleasePending = false;
    this.poseOnlyCalmSteps = 0;
    this.resolvedMotionTrial = guardNextFailure;
  }
}
