import * as THREE from "three";
import type {SolverId} from "../core/contracts.js";
import {parseScenario} from "../core/scenario.js";
import type {ViewerCommandInput, ViewerEvent, ViewerSnapshot} from "./protocol.js";

const app = document.querySelector<HTMLDivElement>("#app"); if (app === null) throw new Error("viewer root is missing");
const renderer = new THREE.WebGLRenderer({antialias: true}); renderer.setPixelRatio(devicePixelRatio); renderer.setSize(innerWidth, innerHeight); renderer.setClearColor(0x000000); app.append(renderer.domElement);
const scene = new THREE.Scene(); const camera = new THREE.OrthographicCamera(-4, 4, 2, -2, -10, 10); camera.position.z = 5;
const paths = new THREE.LineSegments(new THREE.BufferGeometry(), new THREE.LineBasicMaterial({color: 0x148cff, transparent: true, opacity: 0.55})); scene.add(paths);
const points = new THREE.Points(new THREE.BufferGeometry(), new THREE.PointsMaterial({color: 0x3ba5ff, size: 2, sizeAttenuation: false})); scene.add(points);
const foil = new THREE.LineLoop(new THREE.BufferGeometry(), new THREE.LineBasicMaterial({color: 0xe7eef8})); scene.add(foil);
const vortCanvas = document.createElement("canvas"); const vortTexture = new THREE.CanvasTexture(vortCanvas); vortTexture.minFilter = THREE.LinearFilter; vortTexture.magFilter = THREE.LinearFilter; const vortMaterial = new THREE.MeshBasicMaterial({map: vortTexture, transparent: true, opacity: 0.35, depthWrite: false}); const vortPlane = new THREE.Mesh(new THREE.PlaneGeometry(1, 1), vortMaterial); vortPlane.position.z = -1; scene.add(vortPlane);
const overlay = document.createElement("div"); overlay.id = "foilbench-overlay"; overlay.style.cssText = "position:absolute;left:16px;top:12px;white-space:pre;color:#eee;font-size:13px;pointer-events:none;text-shadow:0 1px 2px #000"; app.append(overlay);
const help = document.createElement("div"); help.id = "foilbench-help"; help.style.cssText = "position:absolute;left:16px;bottom:12px;color:#bbb;font-size:12px;pointer-events:none"; help.textContent = "1/2/3 solver  left-drag foil  Space pause  R reset  +/- Re  0 Re reset  [/] tuning  V vorticity  T tracers  C crop"; app.append(help);

let sequence = 0; let latest: ViewerSnapshot | null = null; let renderedRevision = -1; let shutdownAcknowledged = false;
const worker = new Worker(new URL("../worker/simulationWorker.ts", import.meta.url), {type: "module"});
const send = (command: ViewerCommandInput): void => { sequence += 1; worker.postMessage({...command, sequence}); };
worker.onmessage = (event: MessageEvent<ViewerEvent>): void => {
  if (event.data.kind === "shutdown-ack") { shutdownAcknowledged = true; return; }
  latest = event.data;
};
worker.onerror = (event): void => { overlay.textContent = `worker failure: ${event.message}`; };

const query = new URLSearchParams(location.search); const scenarioUrl = query.get("scenario") ?? new URL("../../../../scenarios/airfoil/default.json", import.meta.url).href; const schemaUrl = new URL("../../../../spec/scenario.schema.json", import.meta.url).href;
const [scenarioDocument, schemaDocument] = await Promise.all([fetch(scenarioUrl).then(async (response) => response.json() as Promise<unknown>), fetch(schemaUrl).then(async (response) => response.json() as Promise<object>)]); const scenario = parseScenario(scenarioDocument, schemaDocument); const selected = (query.get("solver") ?? "stable-fluids") as SolverId; send({kind: "initialize", scenario, solverId: selected});

function updateGeometry(geometry: THREE.BufferGeometry, values: Float32Array): void {
  const xyz = new Float32Array(3 * values.length / 2); for (let index = 0; index < values.length / 2; index += 1) { xyz[3 * index] = values[2 * index] ?? 0; xyz[3 * index + 1] = values[2 * index + 1] ?? 0; }
  geometry.setAttribute("position", new THREE.BufferAttribute(xyz, 3));
}

function updateCamera(snapshot: ViewerSnapshot): void {
  const [[x0, x1], [y0, y1]] = snapshot.bounds; const crop = snapshot.cropEnabled ? (scenario.solverOptions.viewerCropCells ?? 0) : 0; const dx = (x1 - x0) / snapshot.resolution[0]; const dy = (y1 - y0) / snapshot.resolution[1]; let left = x0 + crop * dx; let right = x1 - crop * dx; let bottom = y0 + crop * dy; let top = y1 - crop * dy; const domainAspect = (right - left) / (top - bottom); const viewportAspect = innerWidth / Math.max(innerHeight, 1);
  if (viewportAspect > domainAspect) { const extra = 0.5 * ((top - bottom) * viewportAspect - (right - left)); left -= extra; right += extra; } else { const extra = 0.5 * ((right - left) / viewportAspect - (top - bottom)); bottom -= extra; top += extra; }
  camera.left = left; camera.right = right; camera.bottom = bottom; camera.top = top; camera.updateProjectionMatrix();
}

function updateVorticity(snapshot: ViewerSnapshot): void {
  vortPlane.visible = snapshot.vorticityVisible; if (!snapshot.vorticityVisible || snapshot.vorticity.length === 0) return; const [nx, ny] = snapshot.resolution; vortCanvas.width = nx; vortCanvas.height = ny; const context = vortCanvas.getContext("2d"); if (context === null) return; const image = context.createImageData(nx, ny); let scale = 1e-6; for (const value of snapshot.vorticity) scale = Math.max(scale, Math.abs(value));
  for (let index = 0; index < snapshot.vorticity.length; index += 1) { const normalized = Math.max(-1, Math.min(1, (snapshot.vorticity[index] ?? 0) / scale)); image.data[4 * index] = normalized > 0 ? 190 : 20; image.data[4 * index + 1] = 25; image.data[4 * index + 2] = normalized < 0 ? 210 : 20; image.data[4 * index + 3] = Math.round(120 * Math.abs(normalized)); }
  context.putImageData(image, 0, 0); vortTexture.needsUpdate = true; const [[x0, x1], [y0, y1]] = snapshot.bounds; vortPlane.scale.set(x1 - x0, y1 - y0, 1); vortPlane.position.set((x0 + x1) / 2, (y0 + y1) / 2, -1);
}

function updateOverlay(snapshot: ViewerSnapshot): void {
  const rate = snapshot.stepRate === null ? "warming" : snapshot.stepRate.toFixed(1).padStart(5); const throughput = snapshot.simulatedPerWall === null ? "warming" : snapshot.simulatedPerWall.toFixed(2).padStart(5); const metric = (name: string): string => snapshot.diagnostics[name]?.toFixed(3) ?? "—"; const effective = snapshot.diagnostics["effective_reynolds"]; const effectiveText = effective === undefined ? "" : `  Re_eff=${effective.toFixed(0).padStart(6)}`; const paused = snapshot.paused ? "  PAUSED" : "";
  overlay.textContent = `${snapshot.solverId}  t=${snapshot.time.toFixed(2).padStart(6)}  AoA=${snapshot.angleDegrees.toFixed(1).padStart(5)}°  Re=${snapshot.reynolds.toFixed(0).padStart(6)}${effectiveText}  rate=${snapshot.playbackRate.toFixed(2)}x  ${snapshot.solverTuning}  step=${rate}/s  sim/wall=${throughput}  sub=${String(snapshot.substeps).padStart(2)}  max|u|=${snapshot.maxSpeed.toFixed(2)}${paused}\nE=${metric("kinetic_energy")}  Ω=${metric("enstrophy")}  div=${metric("divergence_linf")}  leak=${metric("solid_leakage")}  recovery=${String(snapshot.recoveryEpoch)}  motion=${snapshot.poseOnly ? "pose-only" : "resolved"}\n${snapshot.status}  schedule=${snapshot.scheduleActive ? "on" : "manual"}  tracers=${snapshot.tracerMode}  vort=${snapshot.vorticityVisible ? "on" : "off"}  view=${snapshot.cropEnabled ? "cropped" : "full"}`;
}

function draw(): void {
  requestAnimationFrame(draw); const snapshot = latest; if (snapshot === null) return;
  if (snapshot.revision !== renderedRevision) { renderedRevision = snapshot.revision; updateCamera(snapshot); updateGeometry(paths.geometry, snapshot.pathSegments); updateGeometry(points.geometry, snapshot.tracerPositions); updateGeometry(foil.geometry, snapshot.foilOutline); updateVorticity(snapshot); updateOverlay(snapshot); worker.postMessage({kind: "snapshot-consumed", revision: snapshot.revision}); }
  renderer.render(scene, camera);
}
draw();

let dragging = false; let pendingPose: {readonly angleDegrees: number; readonly timestamp: number} | null = null; let poseFrame = 0;
const pointerAngle = (event: PointerEvent): number => { const rect = renderer.domElement.getBoundingClientRect(); const worldX = camera.left + (event.clientX - rect.left) / rect.width * (camera.right - camera.left); const worldY = camera.top - (event.clientY - rect.top) / rect.height * (camera.top - camera.bottom); return Math.max(-30, Math.min(30, Math.atan2(worldY - (scenario.foil.pivot[1] ?? 0), worldX - (scenario.foil.pivot[0] ?? 0)) * 180 / Math.PI)); };
const flushPose = (): void => { poseFrame = 0; const pose = pendingPose; pendingPose = null; if (pose !== null) send({kind: "set-angle", ...pose}); };
const queuePose = (event: PointerEvent): void => { pendingPose = {angleDegrees: pointerAngle(event), timestamp: performance.now()}; if (poseFrame === 0) poseFrame = requestAnimationFrame(flushPose); };
const releasePointer = (event: PointerEvent): void => { if (!dragging) return; dragging = false; if (poseFrame !== 0) cancelAnimationFrame(poseFrame); flushPose(); if (renderer.domElement.hasPointerCapture(event.pointerId)) renderer.domElement.releasePointerCapture(event.pointerId); send({kind: "release-angle"}); };
renderer.domElement.addEventListener("pointerdown", (event) => { if (event.button !== 0) return; dragging = true; renderer.domElement.setPointerCapture(event.pointerId); pendingPose = {angleDegrees: pointerAngle(event), timestamp: performance.now()}; flushPose(); });
renderer.domElement.addEventListener("pointermove", (event) => { if (dragging) queuePose(event); });
renderer.domElement.addEventListener("pointerup", releasePointer); renderer.domElement.addEventListener("pointercancel", releasePointer); renderer.domElement.addEventListener("lostpointercapture", (event) => { if (dragging) releasePointer(event); });

window.addEventListener("resize", () => { renderer.setSize(innerWidth, innerHeight); renderedRevision = -1; });
window.addEventListener("keydown", (event: KeyboardEvent) => { if (event.key === " ") send({kind: "pause"}); else if (event.key.toLowerCase() === "r") send({kind: "reset"}); else if (event.key === "1" || event.key === "2" || event.key === "3") send({kind: "switch", solverId: (["stable-fluids", "lbm-d2q9", "pic-flip"] as const)[Number(event.key) - 1] ?? "stable-fluids"}); else if (event.key === "+" || event.key === "=") send({kind: "set-reynolds", reynolds: (latest?.reynolds ?? scenario.reynolds) * 10 ** 0.25}); else if (event.key === "-") send({kind: "set-reynolds", reynolds: (latest?.reynolds ?? scenario.reynolds) / 10 ** 0.25}); else if (event.key === "0") send({kind: "set-reynolds", reynolds: scenario.reynolds}); else if (event.key === "[") send({kind: "adjust-tuning", amount: -1}); else if (event.key === "]") send({kind: "adjust-tuning", amount: 1}); else if (event.key.toLowerCase() === "v") send({kind: "toggle-vorticity"}); else if (event.key.toLowerCase() === "t") send({kind: "toggle-tracers"}); else if (event.key.toLowerCase() === "c") send({kind: "toggle-crop"}); });
window.addEventListener("beforeunload", () => { if (!shutdownAcknowledged) send({kind: "shutdown"}); });
