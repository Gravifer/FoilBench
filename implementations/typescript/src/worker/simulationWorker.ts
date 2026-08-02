/// <reference lib="webworker" />
import type {ViewerCommand} from "../viewer/protocol.js";
import {ViewerModel} from "../viewer/model.js";

let model: ViewerModel | null = null; let scheduled = false;
const publish = (): void => { if (model === null) return; const snapshot = model.snapshot(); const buffers = [snapshot.tracerPositions.buffer, snapshot.pathSegments.buffer, snapshot.vorticity.buffer, snapshot.foilOutline.buffer]; postMessage(snapshot, {transfer: buffers}); };
const schedule = (): void => { if (scheduled || model === null || model.paused) return; scheduled = true; setTimeout(loop, 0); };
const loop = (): void => { scheduled = false; if (model === null || model.paused) return; model.step(); publish(); schedule(); };

self.onmessage = (event: MessageEvent<ViewerCommand>): void => { const command = event.data; if (command.kind === "initialize") { model = new ViewerModel(command.scenario, command.solverId); model.appliedCommand = command.sequence; publish(); schedule(); return; } if (model === null) return; model.appliedCommand = command.sequence; if (command.kind === "pause") model.paused = !model.paused; else if (command.kind === "reset") model.reset(); else if (command.kind === "switch") model.switchSolver(command.solverId); else if (command.kind === "set-reynolds") model.setReynolds(command.reynolds); else if (command.kind === "set-angle") model.setAngle(command.angleDegrees, command.timestamp); else if (command.kind === "release-angle") model.releaseAngle(); else if (command.kind === "toggle-vorticity") model.vorticityVisible = !model.vorticityVisible; else if (command.kind === "toggle-crop") model.cropEnabled = !model.cropEnabled; else if (command.kind === "toggle-tracers") model.tracers.mode = model.tracers.mode === "display" ? "flow" : "display"; publish(); schedule(); };
