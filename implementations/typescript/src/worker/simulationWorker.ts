/// <reference lib="webworker" />
import type {SnapshotConsumed, ViewerCommand, ViewerEvent, ViewerSnapshot, ViewerStatusEvent} from "../viewer/protocol.js";
import {ViewerModel} from "../viewer/model.js";
import type {ViewerSnapshotStorage} from "../viewer/model.js";
import {loadRustWasmSolverFactory} from "../wasm/rustWasmSolver.js";

type PoseCommand = Extract<ViewerCommand, {readonly kind: "set-angle"}>;

const minimumCycleMilliseconds = 1000 / 60;
let model: ViewerModel | null = null;
let snapshotStorage: ViewerSnapshotStorage | null = null;
let scheduled = false;
let shuttingDown = false;
let visible = true;
let pendingPose: PoseCommand | null = null;
let poseFlushScheduled = false;
let snapshotInFlightRevision: number | null = null;
let publishPending = false;
let lastCycleWall: number | null = null;
let lastStatusSignature = "";

async function initialize(command: Extract<ViewerCommand, {readonly kind: "initialize"}>): Promise<void> {
  postMessage({
    kind: "status", revision: 0, appliedCommand: command.sequence, solverEpoch: 0,
    solverStateRevision: 0, phase: "warming", status: `loading ${command.backend} backend`,
    recoveryEpoch: 0, recoveryReason: null, recoveryStage: "initialization",
  } satisfies ViewerStatusEvent);
  try {
    const factory = command.backend === "rust-wasm" ? await loadRustWasmSolverFactory() : undefined;
    if (shuttingDown) return;
    model = new ViewerModel(command.scenario, command.solverId, factory);
    snapshotStorage = model.createSnapshotStorage();
    model.appliedCommand = command.sequence;
    lastCycleWall = performance.now();
    publishStatus(true);
    publish();
    schedule();
  } catch (error) {
    postMessage({
      kind: "status", revision: 0, appliedCommand: command.sequence, solverEpoch: 0,
      solverStateRevision: 0, phase: "failed",
      status: `backend initialization failed: ${error instanceof Error ? error.message : String(error)}`,
      recoveryEpoch: 0, recoveryReason: null, recoveryStage: "initialization",
    } satisfies ViewerStatusEvent);
  }
}

function postSnapshot(snapshot: ViewerSnapshot): void {
  postMessage(snapshot satisfies ViewerEvent);
}

function publishStatus(force = false): void {
  if (model === null || shuttingDown) return;
  const session = model.sessionState();
  const signature = `${String(model.appliedCommand)}|${session.phase}|${model.status}|${String(session.recoveryEpoch)}|${session.recoveryReason ?? ""}|${session.recoveryStage ?? ""}`;
  if (!force && signature === lastStatusSignature) return;
  lastStatusSignature = signature;
  postMessage({
    kind: "status",
    revision: model.revision,
    appliedCommand: model.appliedCommand,
    solverEpoch: model.solverEpoch,
    solverStateRevision: model.solver.stateRevision,
    phase: session.phase,
    status: model.status,
    recoveryEpoch: session.recoveryEpoch,
    recoveryReason: session.recoveryReason,
    recoveryStage: session.recoveryStage,
  } satisfies ViewerStatusEvent);
}

function publish(): void {
  if (model === null || shuttingDown) return;
  publishStatus();
  if (!visible) { publishPending = true; return; }
  if (snapshotInFlightRevision !== null) { publishPending = true; return; }
  try {
    const snapshot = model.snapshot(snapshotStorage ?? undefined);
    postSnapshot(snapshot);
    snapshotInFlightRevision = snapshot.revision;
    publishPending = false;
  } catch (error) {
    snapshotInFlightRevision = null;
    model.status = `snapshot failure: ${error instanceof Error ? error.name : "unknown"}; flow retained`;
    publishStatus(true);
  }
}

function applyPendingPose(): void {
  if (model === null || pendingPose === null) return;
  const command = pendingPose;
  pendingPose = null;
  model.appliedCommand = command.sequence;
  model.setAngle(command.angleDegrees, command.timestamp);
}

function schedule(delayMilliseconds = 0): void {
  if (scheduled || model === null || model.paused || shuttingDown) return;
  scheduled = true;
  setTimeout(loop, Math.max(0, delayMilliseconds));
}

function loop(): void {
  scheduled = false;
  if (model === null || model.paused || shuttingDown) return;
  const started = performance.now();
  try {
    applyPendingPose();
    const advanced = model.step();
    const completed = performance.now();
    if (advanced > 0 && lastCycleWall !== null) model.recordOwnerCycle(advanced, Math.max((completed - lastCycleWall) / 1000, 1e-9));
    lastCycleWall = completed;
    publish();
    schedule(Math.max(0, minimumCycleMilliseconds - (completed - started)));
  } catch (error) {
    model.paused = true;
    model.status = `owner failure: ${error instanceof Error ? error.name : "unknown"}; paused`;
    publishStatus(true);
    publish();
  }
}

function queuePose(command: PoseCommand): void {
  pendingPose = command;
  if (poseFlushScheduled) return;
  poseFlushScheduled = true;
  setTimeout(() => {
    poseFlushScheduled = false;
    if (shuttingDown) return;
    applyPendingPose();
    publish();
    schedule();
  }, 0);
}

function acknowledgeSnapshot(message: SnapshotConsumed): void {
  if (message.revision !== snapshotInFlightRevision) return;
  snapshotInFlightRevision = null;
  if (publishPending) publish();
}

function shutdown(command: Extract<ViewerCommand, {readonly kind: "shutdown"}>): void {
  applyPendingPose();
  shuttingDown = true;
  if (model !== null) model.appliedCommand = command.sequence;
  postMessage({kind: "shutdown-ack", appliedCommand: command.sequence} satisfies ViewerEvent);
  close();
}

self.onmessage = (event: MessageEvent<ViewerCommand | SnapshotConsumed>): void => {
  const command = event.data;
  if (command.kind === "snapshot-consumed") { acknowledgeSnapshot(command); return; }
  if (command.kind === "shutdown") { shutdown(command); return; }
  if (shuttingDown) return;
  if (command.kind === "initialize") {
    void initialize(command);
    return;
  }
  if (model === null) return;
  if (command.kind === "set-angle") { queuePose(command); return; }
  try {
    applyPendingPose();
    model.appliedCommand = command.sequence;
    if (command.kind === "pause") { model.paused = !model.paused; lastCycleWall = model.paused ? null : performance.now(); }
    else if (command.kind === "reset") { model.reset(); lastCycleWall = performance.now(); }
    else if (command.kind === "switch") { model.switchSolver(command.solverId); lastCycleWall = performance.now(); }
    else if (command.kind === "set-reynolds") { model.setReynolds(command.reynolds); lastCycleWall = performance.now(); }
    else if (command.kind === "release-angle") model.releaseAngle();
    else if (command.kind === "adjust-tuning") model.adjustSolverTuning(command.amount);
    else if (command.kind === "toggle-vorticity") model.toggleVorticity();
    else if (command.kind === "toggle-crop") model.toggleCrop();
    else if (command.kind === "toggle-diagnostics") model.toggleDiagnostics();
    else if (command.kind === "visibility") { visible = command.visible; if (visible) publishPending = true; }
    else model.toggleTracers();
  } catch (error) {
    model.paused = true;
    model.status = `command failure: ${error instanceof Error ? error.name : "unknown"}; paused`;
  }
  publishStatus(true);
  publish();
  schedule();
};
