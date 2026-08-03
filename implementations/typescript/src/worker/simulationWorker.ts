/// <reference lib="webworker" />
import type {SnapshotConsumed, ViewerCommand, ViewerEvent, ViewerSnapshot} from "../viewer/protocol.js";
import {ViewerModel} from "../viewer/model.js";

type PoseCommand = Extract<ViewerCommand, {readonly kind: "set-angle"}>;

let model: ViewerModel | null = null;
let scheduled = false;
let shuttingDown = false;
let pendingPose: PoseCommand | null = null;
let poseFlushScheduled = false;
let snapshotInFlight = false;
let publishPending = false;
let lastCycleWall: number | null = null;

function postSnapshot(snapshot: ViewerSnapshot): void {
  const buffers = [snapshot.tracerPositions.buffer, snapshot.pathSegments.buffer, snapshot.vorticity.buffer, snapshot.foilOutline.buffer];
  postMessage(snapshot satisfies ViewerEvent, {transfer: buffers});
}

function publish(): void {
  if (model === null || shuttingDown) return;
  if (snapshotInFlight) { publishPending = true; return; }
  try {
    const snapshot = model.snapshot(); snapshotInFlight = true; publishPending = false; postSnapshot(snapshot);
  } catch (error) {
    model.status = `snapshot failure: ${error instanceof Error ? error.name : "unknown"}; flow retained`;
  }
}

function applyPendingPose(): void {
  if (model === null || pendingPose === null) return;
  const command = pendingPose; pendingPose = null; model.appliedCommand = command.sequence; model.setAngle(command.angleDegrees, command.timestamp);
}

function schedule(): void {
  if (scheduled || model === null || model.paused || shuttingDown) return;
  scheduled = true; setTimeout(loop, 0);
}

function loop(): void {
  scheduled = false; if (model === null || model.paused || shuttingDown) return;
  applyPendingPose(); const advanced = model.step(); const completed = performance.now();
  if (advanced > 0 && lastCycleWall !== null) model.recordOwnerCycle(advanced, Math.max((completed - lastCycleWall) / 1000, 1e-9));
  lastCycleWall = completed; publish(); schedule();
}

function queuePose(command: PoseCommand): void {
  pendingPose = command;
  if (poseFlushScheduled) return;
  poseFlushScheduled = true;
  setTimeout(() => { poseFlushScheduled = false; if (shuttingDown) return; applyPendingPose(); publish(); schedule(); }, 0);
}

function acknowledgeSnapshot(message: SnapshotConsumed): void {
  void message.revision; snapshotInFlight = false;
  if (publishPending) publish();
}

function shutdown(command: Extract<ViewerCommand, {readonly kind: "shutdown"}>): void {
  applyPendingPose(); shuttingDown = true; if (model !== null) model.appliedCommand = command.sequence;
  postMessage({kind: "shutdown-ack", appliedCommand: command.sequence} satisfies ViewerEvent); close();
}

self.onmessage = (event: MessageEvent<ViewerCommand | SnapshotConsumed>): void => {
  const command = event.data;
  if (command.kind === "snapshot-consumed") { acknowledgeSnapshot(command); return; }
  if (command.kind === "shutdown") { shutdown(command); return; }
  if (shuttingDown) return;
  if (command.kind === "initialize") { model = new ViewerModel(command.scenario, command.solverId); model.appliedCommand = command.sequence; lastCycleWall = performance.now(); publish(); schedule(); return; }
  if (model === null) return;
  if (command.kind === "set-angle") { queuePose(command); return; }
  try {
    applyPendingPose(); model.appliedCommand = command.sequence;
    if (command.kind === "pause") { model.paused = !model.paused; lastCycleWall = model.paused ? null : performance.now(); }
    else if (command.kind === "reset") { model.reset(); lastCycleWall = performance.now(); }
    else if (command.kind === "switch") { model.switchSolver(command.solverId); lastCycleWall = performance.now(); }
    else if (command.kind === "set-reynolds") { model.setReynolds(command.reynolds); lastCycleWall = performance.now(); }
    else if (command.kind === "release-angle") model.releaseAngle();
    else if (command.kind === "adjust-tuning") model.adjustSolverTuning(command.amount);
    else if (command.kind === "toggle-vorticity") model.toggleVorticity();
    else if (command.kind === "toggle-crop") model.toggleCrop();
    else model.tracers.mode = model.tracers.mode === "display" ? "flow" : "display";
  } catch (error) {
    model.paused = true; model.status = `command failure: ${error instanceof Error ? error.name : "unknown"}; paused`;
  }
  publish(); schedule();
};
