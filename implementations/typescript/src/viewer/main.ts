import {isSolverId} from "../core/contracts.js";
import {parseScenario} from "../core/scenario.js";
import type {SolverBackend, ViewerSnapshot, ViewerStatusEvent} from "./protocol.js";
import {FoilSceneController} from "./sceneController.js";
import {fuseViewerStatus} from "./statusFusion.js";
import {ViewerWorkerClient} from "./workerClient.js";

const app = document.querySelector<HTMLDivElement>("#app");
if (app === null) throw new Error("viewer root is missing");

const scene = new FoilSceneController(app);
scene.resize(innerWidth, innerHeight);
const overlay = document.createElement("div");
overlay.id = "foilbench-overlay";
overlay.style.cssText = "position:absolute;left:16px;top:12px;white-space:pre;color:#eee;font-size:13px;pointer-events:none;text-shadow:0 1px 2px #000";
app.append(overlay);
const help = document.createElement("div");
help.id = "foilbench-help";
help.style.cssText = "position:absolute;left:16px;bottom:12px;color:#bbb;font-size:12px;pointer-events:none";
help.textContent = "1/2/3 solver  left-drag foil  Space pause  R reset  +/- Re  0 Re reset  [/] tuning  V vorticity  D diagnostics  T tracers  C crop";
app.append(help);

const query = new URLSearchParams(location.search);
const scenarioUrl = query.get("scenario") ?? new URL("../../../../scenarios/airfoil/default.json", import.meta.url).href;
const schemaUrl = new URL("../../../../spec/schemas/scenario.schema.json", import.meta.url).href;
const [scenarioDocument, schemaDocument] = await Promise.all([
  fetch(scenarioUrl).then(async (response) => response.json() as Promise<unknown>),
  fetch(schemaUrl).then(async (response) => response.json() as Promise<object>),
]);
const scenario = parseScenario(scenarioDocument, schemaDocument);
const requestedSolver = query.get("solver") ?? "stable-fluids";
if (!isSolverId(requestedSolver)) throw new Error(`unsupported solver id: ${requestedSolver}`);
const backendValue = query.get("backend") ?? "typescript";
if (backendValue !== "typescript" && backendValue !== "rust-wasm") throw new Error(`unsupported backend: ${backendValue}`);
const backend: SolverBackend = backendValue;

const client = new ViewerWorkerClient();
let latest: ViewerSnapshot | null = null;
let latestStatus: ViewerStatusEvent | null = null;
let renderedRevision = -1;
let dragging = false;

function updateOverlay(snapshot: ViewerSnapshot): void {
  const rate = snapshot.stepRate === null ? "warming" : snapshot.stepRate.toFixed(1).padStart(5);
  const throughput = snapshot.simulatedPerWall === null ? "warming" : snapshot.simulatedPerWall.toFixed(2).padStart(5);
  const metric = (name: string): string => snapshot.diagnostics[name]?.toFixed(3) ?? "—";
  const effective = snapshot.diagnostics["effective_reynolds"];
  const effectiveText = effective === undefined ? "" : `  Re_eff=${effective.toFixed(0).padStart(6)}`;
  const paused = snapshot.paused ? "  PAUSED" : "";
  const control = fuseViewerStatus(snapshot, latestStatus);
  const pending = control.pendingStatus === null ? "" : `\n${control.pendingStatus}`;
  const displayedAoa = Math.abs(snapshot.angleDegrees) < 0.05 ? 0 : -snapshot.angleDegrees;
  overlay.textContent = `${snapshot.solverId} [${backend}]  t=${snapshot.time.toFixed(2).padStart(6)}  AoA=${displayedAoa.toFixed(1).padStart(5)}°  Re=${snapshot.reynolds.toFixed(0).padStart(6)}${effectiveText}  rate=${snapshot.playbackRate.toFixed(2)}x  ${snapshot.solverTuning}  step=${rate}/s  sim/wall=${throughput}  sub=${String(snapshot.substeps).padStart(2)}  max|u|=${snapshot.maxSpeed.toFixed(2)}${paused}\nE=${metric("kinetic_energy")}  Ω=${metric("enstrophy")}  div=${metric("divergence_linf")}  leak=${metric("solid_leakage")}  recovery=${String(control.recoveryEpoch)}  motion=${snapshot.motionMode}  phase=${control.phase}\n${control.status}  schedule=${snapshot.scheduleActive ? "on" : "manual"}  tracers=${snapshot.tracerMode}  vort=${snapshot.vorticityVisible ? "on" : "off"}  diag=${snapshot.diagnosticMode}  view=${snapshot.cropEnabled ? "cropped" : "full"}${pending}`;
}

client.subscribe((state) => {
  if (state.status !== null) latestStatus = state.status;
  if (state.snapshot !== null) latest = state.snapshot;
  if (state.error !== null) overlay.textContent = state.error;
  else if (latest === null && state.status !== null) overlay.textContent = state.status.status;
});
client.initialize(scenario, requestedSolver, backend);

function draw(): void {
  requestAnimationFrame(draw);
  const snapshot = latest;
  if (snapshot === null) return;
  if (snapshot.revision !== renderedRevision) {
    renderedRevision = snapshot.revision;
    scene.render(snapshot, scenario);
    client.acknowledgeSnapshot(snapshot.revision);
  } else scene.draw();
  updateOverlay(snapshot);
}
draw();

const releasePointer = (event: PointerEvent): void => {
  if (!dragging) return;
  dragging = false;
  client.releaseAngle();
  if (scene.canvas.hasPointerCapture(event.pointerId)) scene.canvas.releasePointerCapture(event.pointerId);
};
scene.canvas.addEventListener("pointerdown", (event) => {
  if (event.button !== 0) return;
  dragging = true;
  scene.canvas.setPointerCapture(event.pointerId);
});
scene.canvas.addEventListener("pointermove", (event) => {
  if (dragging) client.queueAngle(scene.pointerAngle(event, scenario));
});
scene.canvas.addEventListener("pointerup", releasePointer);
scene.canvas.addEventListener("pointercancel", releasePointer);
scene.canvas.addEventListener("lostpointercapture", (event) => {
  if (dragging) releasePointer(event);
});

window.addEventListener("resize", () => {
  scene.resize(innerWidth, innerHeight);
  renderedRevision = -1;
});
window.addEventListener("keydown", (event: KeyboardEvent) => {
  if (event.key === " ") client.send({kind: "pause"});
  else if (event.key.toLowerCase() === "r") client.send({kind: "reset"});
  else if (event.key === "1" || event.key === "2" || event.key === "3") client.send({kind: "switch", solverId: (["stable-fluids", "lbm-d2q9", "pic-flip"] as const)[Number(event.key) - 1] ?? "stable-fluids"});
  else if (event.key === "+" || event.key === "=") client.send({kind: "set-reynolds", reynolds: (latest?.reynolds ?? scenario.reynolds) * 10 ** 0.25});
  else if (event.key === "-") client.send({kind: "set-reynolds", reynolds: (latest?.reynolds ?? scenario.reynolds) / 10 ** 0.25});
  else if (event.key === "0") client.send({kind: "set-reynolds", reynolds: scenario.reynolds});
  else if (event.key === "[") client.send({kind: "adjust-tuning", amount: -1});
  else if (event.key === "]") client.send({kind: "adjust-tuning", amount: 1});
  else if (event.key.toLowerCase() === "v") client.send({kind: "toggle-vorticity"});
  else if (event.key.toLowerCase() === "d") client.send({kind: "toggle-diagnostics"});
  else if (event.key.toLowerCase() === "t") client.send({kind: "toggle-tracers"});
  else if (event.key.toLowerCase() === "c") client.send({kind: "toggle-crop"});
});
document.addEventListener("visibilitychange", () => { client.setVisible(document.visibilityState === "visible"); });
const requestShutdown = (): void => { client.shutdown(); };
window.addEventListener("pagehide", requestShutdown);
window.addEventListener("beforeunload", requestShutdown);
