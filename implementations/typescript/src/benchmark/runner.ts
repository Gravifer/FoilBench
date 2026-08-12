import Ajv2020 from "ajv/dist/2020.js";
import {chromium} from "playwright";
import {execFileSync} from "node:child_process";
import {mkdir, readFile, writeFile} from "node:fs/promises";
import {cpus, platform, release} from "node:os";
import {dirname, isAbsolute, join, resolve} from "node:path";
import {fileURLToPath} from "node:url";
import {createServer} from "vite";
import type {CanonicalFlowState, Scenario, SolverId, StepReport} from "../core/contracts.js";
import {decodeNpy, semanticCOrder} from "../core/npy.js";
import {parseScenario, validateDocument} from "../core/scenario.js";
import type {BenchmarkMatrix, BrowserRunRequest, BrowserRunResult} from "./types.js";
import type {BrowserCanonicalSnapshot} from "./types.js";

interface RawMatrix {schema_version: 1; id: string; scenario: string; solvers: SolverId[]; resolutions: [number, number][]; duration: number; repetitions: number; save_snapshots: boolean}
function repositoryRoot(): string { return resolve(dirname(fileURLToPath(import.meta.url)), "../../../.."); }
async function json(path: string): Promise<unknown> { return JSON.parse(await readFile(path, "utf8")) as unknown; }
function percentile(values: readonly number[], fraction: number): number { if (values.length === 0) return 0; const sorted = [...values].sort((a, b) => a - b); const position = fraction * (sorted.length - 1); const lower = Math.floor(position); const upper = Math.ceil(position); const a = sorted[lower] ?? 0; const b = sorted[upper] ?? a; return a + (b - a) * (position - lower); }
function stepArtifact(report: StepReport | null): Record<string, unknown> | null { return report === null ? null : {requested_dt: report.requestedDt, advanced_dt: report.advancedDt, substeps: report.substeps, max_speed: report.maxSpeed, state_revision: report.stateRevision, evidence: report.evidence, warnings: report.warnings}; }
const PHYSICAL_IDENTITY_FIELDS = ["bounds", "periodic_axes", "reynolds", "effective_reynolds", "solver_configuration", "freestream", "foil", "control_history", "requested_duration", "output_dt", "seed"] as const;

function canonicalize(value: unknown): unknown {
  if (Array.isArray(value)) return value.map(canonicalize);
  if (value !== null && typeof value === "object") {
    return Object.fromEntries(Object.entries(value as Record<string, unknown>).sort(([left], [right]) => left.localeCompare(right)).map(([key, child]) => [key, canonicalize(child)]));
  }
  return value;
}

function physicalIdentity(value: Readonly<Record<string, unknown>>): unknown {
  return canonicalize(Object.fromEntries(PHYSICAL_IDENTITY_FIELDS.map((field) => [field, value[field]])));
}

function physicalIdentitiesMatch(left: unknown, right: unknown, precision: unknown): boolean {
  if (typeof left === "number" && typeof right === "number") {
    if (Number.isInteger(left) && Number.isInteger(right)) return left === right;
    const tolerance = precision === "float32" ? 2e-6 : 2e-12;
    return Math.abs(left - right) <= tolerance * Math.max(1, Math.abs(left), Math.abs(right));
  }
  if (Array.isArray(left) && Array.isArray(right)) return left.length === right.length && left.every((child, index) => physicalIdentitiesMatch(child, right[index], precision));
  if (left !== null && right !== null && typeof left === "object" && typeof right === "object" && !Array.isArray(left) && !Array.isArray(right)) {
    const leftEntries = Object.entries(left as Record<string, unknown>).sort(([a], [b]) => a.localeCompare(b));
    const rightEntries = Object.entries(right as Record<string, unknown>).sort(([a], [b]) => a.localeCompare(b));
    return leftEntries.length === rightEntries.length && leftEntries.every(([key, child], index) => {
      const rightEntry = rightEntries[index];
      return rightEntry !== undefined && key === rightEntry[0] && physicalIdentitiesMatch(child, rightEntry[1], precision);
    });
  }
  return typeof left === typeof right && left === right;
}

function solverConfiguration(scenario: Scenario): Record<string, unknown> {
  return {
    initial_condition: scenario.solverOptions.initialCondition ?? "freestream",
    stable_advection: scenario.solverOptions.stableAdvection ?? "maccormack",
    stable_face_advection: scenario.solverOptions.stableFaceAdvection ?? false,
    stable_cfl: scenario.solverOptions.stableCfl ?? 0.7,
    pressure_tolerance: scenario.solverOptions.pressureTolerance ?? 1e-5,
    pressure_max_iterations: scenario.solverOptions.pressureMaxIterations ?? 640,
    pic_flip_blend: scenario.solverOptions.picFlipBlend ?? 0.95,
    pic_population_interval: scenario.solverOptions.picPopulationInterval ?? 8,
    pic_cfl: scenario.solverOptions.picCfl ?? 0.75,
  };
}

function requireFinite(value: unknown, path = "result"): void {
  if (typeof value === "number" && !Number.isFinite(value)) throw new TypeError(`${path} contains a non-finite number`);
  if (Array.isArray(value)) value.forEach((child, index) => requireFinite(child, `${path}[${String(index)}]`));
  else if (value !== null && typeof value === "object") for (const [key, child] of Object.entries(value)) requireFinite(child, `${path}.${key}`);
}

export function validateResultSemantics(value: Readonly<Record<string, unknown>>): void {
  requireFinite(value);
  const success = value["success"] === true;
  const lastStep = value["last_step"];
  const rawSteps = value["step_seconds"];
  const steps: readonly unknown[] = Array.isArray(rawSteps) ? rawSteps : [];
  const finalRevision = value["final_state_revision"];
  const precision = value["precision"];
  const requested = Number(value["requested_duration"]);
  const simulated = Number(value["simulated_duration"]);
  const tolerance = (precision === "float32" ? 1e-6 : 1e-12) * Math.max(1, Math.abs(requested));
  if (success) {
    if (value["failure"] !== null || lastStep === null || typeof lastStep !== "object" || !Array.isArray(rawSteps) || steps.length === 0) throw new TypeError("successful benchmark result lacks completed-step semantics");
    const stepRevision = (lastStep as Record<string, unknown>)["state_revision"];
    if (value["diagnostic_state_revision"] !== finalRevision || stepRevision !== finalRevision) throw new TypeError("successful benchmark result contains stale revision evidence");
    if (Math.abs(simulated - requested) > tolerance) throw new TypeError("successful benchmark result did not complete requested duration");
  } else if (value["failure"] === null || typeof value["failure"] !== "object") throw new TypeError("failed benchmark result lacks structured failure evidence");
  if (steps.length > 0) {
    const stepSeconds = steps.map(Number);
    const totalWall = stepSeconds.reduce((left, right) => left + right, 0);
    const resolution = value["resolution"] as readonly number[];
    const cells = resolution.reduce((left, right) => left * right, 1);
    const substeps = Number(value["substeps"]);
    const diagnostics = value["diagnostics"] as Readonly<Record<string, unknown>>;
    const particleCount = Number(diagnostics["particle_count"] ?? 0);
    const expected: Readonly<Record<string, number>> = {
      median_step_seconds: percentile(stepSeconds, 0.5),
      p95_step_seconds: percentile(stepSeconds, 0.95),
      simulated_seconds_per_wall_second: simulated / totalWall,
      cell_updates_per_second: cells * substeps / totalWall,
      particle_updates_per_second: particleCount * substeps / totalWall,
    };
    for (const [field, expectedValue] of Object.entries(expected)) {
      const actual = Number(value[field]);
      if (Math.abs(actual - expectedValue) > 1e-12 + 1e-10 * Math.max(Math.abs(actual), Math.abs(expectedValue))) throw new TypeError(`benchmark result contains inconsistent derived field ${field}`);
    }
  }
  if ((value["memory_measurement"] === "unavailable") !== (value["peak_rss_bytes"] === null)) throw new TypeError("memory measurement kind and RSS value disagree");
}

export async function loadMatrix(path: string): Promise<BenchmarkMatrix> { const root = repositoryRoot(); const document = await json(isAbsolute(path) ? path : join(root, path)); validateDocument(document, await json(join(root, "spec/benchmark-matrix.schema.json")) as object); const raw = document as RawMatrix; return {id: raw.id, scenario: raw.scenario, solvers: raw.solvers, resolutions: raw.resolutions, duration: raw.duration, repetitions: raw.repetitions, saveSnapshots: raw.save_snapshots}; }

export async function runBrowserMatrix(matrixPath: string, outputRoot?: string): Promise<string> { const root = repositoryRoot(); const matrix = await loadMatrix(matrixPath); const scenario = parseScenario(await json(join(root, matrix.scenario)), await json(join(root, "spec/scenario.schema.json")) as object); const resultSchema = await json(join(root, "spec/result.schema.json")) as object; const validator = new Ajv2020({strict: true, allErrors: true}).compile(resultSchema); const destination = outputRoot === undefined ? join(root, "results", matrix.id, new Date().toISOString().replaceAll(/[:.]/g, "-")) : isAbsolute(outputRoot) ? outputRoot : join(root, outputRoot); await mkdir(destination, {recursive: true}); const server = await createServer({root: resolve(root, "implementations/typescript"), server: {host: "127.0.0.1", port: 0}}); await server.listen(); const address = server.httpServer?.address(); if (address === null || typeof address === "string" || address === undefined) throw new Error("Vite benchmark server did not bind"); const runtimeStarted = performance.now(); const browser = await chromium.launch({headless: true}); const runtimeStartupSeconds = (performance.now() - runtimeStarted) / 1000; const page = await browser.newPage(); await page.goto(`http://127.0.0.1:${String(address.port)}/benchmark.html`); const rows: string[][] = [];
  try { for (const resolution of matrix.resolutions) for (const solverId of matrix.solvers) for (let repetition = 1; repetition <= matrix.repetitions; repetition += 1) { const selected: Scenario = {...scenario, domain: {...scenario.domain, resolution}}; const request: BrowserRunRequest = {scenario: selected, solverId, duration: matrix.duration}; const response = await page.evaluate(async (value) => { const browser = globalThis as unknown as {runFoilBench(request: BrowserRunRequest): Promise<{workerStartupSeconds: number; result: BrowserRunResult}>}; return browser.runFoilBench(value); }, request); const run: BrowserRunResult = response.result; const totalWall = run.stepSeconds.reduce((left, right) => left + right, 0); const median = percentile(run.stepSeconds, 0.5); const p95 = percentile(run.stepSeconds, 0.95); const cells = resolution[0] * resolution[1]; const particleCount = run.diagnostics["particle_count"] ?? 0; const effectiveReynolds = Number(run.diagnostics["effective_reynolds"] ?? run.lastStep?.evidence["effective_reynolds"] ?? selected.reynolds); const result = {schema_version: 1, contract_id: "foilbench-phase2-v1", contract_revision: 4, benchmark_matrix_id: matrix.id, scenario_id: selected.id, repetition, language: "typescript", solver: solverId, git_commit: gitCommit(root), machine: {platform: `${platform()} ${release()}`, node: process.version, browser: "chromium", logical_cpus: cpus().length}, precision: selected.precision, resolution: [...resolution], bounds: selected.domain.bounds, periodic_axes: selected.domain.periodicAxes, reynolds: selected.reynolds, effective_reynolds: effectiveReynolds, solver_configuration: solverConfiguration(selected), freestream: selected.freestream, foil: selected.foil, control_history: selected.controls.map((control) => ({time: control.time, angle_degrees: control.angleDegrees})), requested_duration: matrix.duration, simulated_duration: run.simulatedSeconds, output_dt: selected.outputDt, seed: selected.seed, initialization_seconds: run.initializationSeconds, cold_step_seconds: run.coldStepSeconds, step_seconds: run.stepSeconds, median_step_seconds: median, p95_step_seconds: p95, simulated_seconds_per_wall_second: totalWall > 0 ? run.simulatedSeconds / totalWall : 0, cell_updates_per_second: totalWall > 0 ? cells * run.substeps / totalWall : 0, particle_updates_per_second: totalWall > 0 ? particleCount * run.substeps / totalWall : 0, peak_rss_bytes: null, memory_measurement: "unavailable", runtime_startup_seconds: runtimeStartupSeconds, worker_startup_seconds: response.workerStartupSeconds, substeps: run.substeps, final_state_revision: run.finalStateRevision, diagnostic_state_revision: run.diagnosticStateRevision, last_step: stepArtifact(run.lastStep), diagnostics: run.diagnostics, success: run.success, failure: run.failure, warnings: run.warnings}; if (!validator(result)) throw new TypeError(new Ajv2020().errorsText(validator.errors)); validateResultSemantics(result); const stem = `${solverId}-${String(resolution[0])}x${String(resolution[1])}-r${String(repetition)}`; await writeFile(join(destination, `${stem}.json`), JSON.stringify(result, null, 2), "utf8"); if (matrix.saveSnapshots && run.success && run.snapshot !== null) await saveCanonicalSnapshot(join(destination, `${stem}-state`), run.snapshot, solverId, await json(join(root, "spec/canonical-manifest.schema.json")) as object); rows.push([solverId, `${String(resolution[0])}x${String(resolution[1])}`, String(repetition), String(median), String(p95), String(result.simulated_seconds_per_wall_second), String(result.success)]); } }
  finally { await browser.close(); await server.close(); }
  await writeFile(join(destination, "summary.csv"), [["solver", "resolution", "repetition", "median_step_seconds", "p95_step_seconds", "simulated_seconds_per_wall_second", "success"], ...rows].map((row) => row.join(",")).join("\n") + "\n", "utf8"); return destination; }
function gitCommit(root: string): string { try { return execFileSync("git", ["rev-parse", "HEAD"], {cwd: root, encoding: "utf8"}).trim(); } catch { return "unknown"; } }

function encodeNpy(values: readonly number[], precision: "float32" | "float64", shape: readonly number[]): Buffer { const bytes = precision === "float32" ? 4 : 8; const descriptor = precision === "float32" ? "<f4" : "<f8"; const shapeText = shape.length === 1 ? `${String(shape[0])},` : shape.map(String).join(", "); const prefixLength = 10; let header = `{'descr': '${descriptor}', 'fortran_order': False, 'shape': (${shapeText}), }`; const padding = (16 - ((prefixLength + header.length + 1) % 16)) % 16; header += " ".repeat(padding) + "\n"; const output = Buffer.alloc(prefixLength + header.length + bytes * values.length); output[0] = 0x93; output.write("NUMPY", 1, "ascii"); output[6] = 1; output[7] = 0; output.writeUInt16LE(header.length, 8); output.write(header, prefixLength, "latin1"); const view = new DataView(output.buffer, output.byteOffset + prefixLength + header.length, bytes * values.length); for (let index = 0; index < values.length; index += 1) { const value = values[index] ?? 0; if (bytes === 4) view.setFloat32(index * bytes, value, true); else view.setFloat64(index * bytes, value, true); } return output; }

async function saveCanonicalSnapshot(directory: string, snapshot: BrowserCanonicalSnapshot, solverId: SolverId, schema: object): Promise<void> { await mkdir(directory, {recursive: true}); const [nx, ny] = snapshot.resolution; if (nx === undefined || ny === undefined) throw new RangeError("2D snapshot resolution missing"); const velocityMetadata = {file: "velocity.npy", axes: ["z", "y", "x", "component"], order: "C"}; const densityMetadata = snapshot.density === null ? null : {file: "density.npy", axes: ["z", "y", "x"], order: "C"}; const manifest = {schema_version: 1, dimension: 2, bounds: snapshot.bounds, resolution: snapshot.resolution, periodic_axes: snapshot.periodicAxes, time: snapshot.time, precision: snapshot.precision, angle_degrees: snapshot.angleDegrees, angular_velocity_degrees: snapshot.angularVelocityDegrees, source_language: "typescript", source_solver: solverId, velocity: velocityMetadata, density: densityMetadata}; validateDocument(manifest, schema); await writeFile(join(directory, "velocity.npy"), encodeNpy(snapshot.velocity, snapshot.precision, [1, ny, nx, 2])); if (snapshot.density !== null) await writeFile(join(directory, "density.npy"), encodeNpy(snapshot.density, snapshot.precision, [1, ny, nx])); await writeFile(join(directory, "manifest.json"), JSON.stringify(manifest, null, 2), "utf8"); }

export async function loadCanonicalSnapshot(directory: string): Promise<CanonicalFlowState> {
  const root = repositoryRoot();
  const manifest = await json(join(directory, "manifest.json")) as Record<string, unknown>;
  validateDocument(manifest, await json(join(root, "spec/canonical-manifest.schema.json")) as object);
  const velocityMetadata = manifest["velocity"] as {file: string; axes: readonly string[]; order: "C" | "F"};
  if (velocityMetadata.file !== "velocity.npy" || JSON.stringify(velocityMetadata.axes) !== JSON.stringify(["z", "y", "x", "component"])) throw new TypeError("invalid canonical velocity metadata");
  const velocityBytes = await readFile(join(directory, velocityMetadata.file));
  const velocityNpy = decodeNpy(velocityBytes.buffer.slice(velocityBytes.byteOffset, velocityBytes.byteOffset + velocityBytes.byteLength));
  const precision = manifest["precision"] as "float32" | "float64";
  const resolution = manifest["resolution"] as readonly number[];
  const dimension = manifest["dimension"] as 2 | 3;
  const expectedVelocityShape = [dimension === 2 ? 1 : resolution[2], resolution[1], resolution[0], dimension];
  if (JSON.stringify(velocityNpy.shape) !== JSON.stringify(expectedVelocityShape)) throw new TypeError("canonical velocity shape disagrees with manifest");
  if (velocityNpy.precision !== precision || velocityMetadata.order !== (velocityNpy.fortranOrder ? "F" : "C")) throw new TypeError("canonical velocity dtype/order disagrees with manifest");
  let density = null;
  const densityMetadata = manifest["density"] as {file: string; axes: readonly string[]; order: "C" | "F"} | null;
  if (densityMetadata !== null) {
    if (densityMetadata.file !== "density.npy" || JSON.stringify(densityMetadata.axes) !== JSON.stringify(["z", "y", "x"])) throw new TypeError("invalid canonical density metadata");
    const densityBytes = await readFile(join(directory, densityMetadata.file));
    const densityNpy = decodeNpy(densityBytes.buffer.slice(densityBytes.byteOffset, densityBytes.byteOffset + densityBytes.byteLength));
    if (JSON.stringify(densityNpy.shape) !== JSON.stringify(expectedVelocityShape.slice(0, -1))) throw new TypeError("canonical density shape disagrees with manifest");
    if (densityNpy.precision !== precision || densityMetadata.order !== (densityNpy.fortranOrder ? "F" : "C")) throw new TypeError("canonical density dtype/order disagrees with manifest");
    density = semanticCOrder(densityNpy);
  }
  return {
    schemaVersion: 1, dimension,
    bounds: manifest["bounds"] as readonly (readonly [number, number])[], resolution,
    periodicAxes: manifest["periodic_axes"] as readonly ("x" | "y" | "z")[], time: Number(manifest["time"]), precision,
    angleDegrees: Number(manifest["angle_degrees"]), angularVelocityDegrees: Number(manifest["angular_velocity_degrees"]),
    sourceLanguage: String(manifest["source_language"]), sourceSolver: String(manifest["source_solver"]),
    velocity: semanticCOrder(velocityNpy), density,
  };
}

export async function assertCompleteMatrices(results: readonly Readonly<Record<string, unknown>>[], requiredLanguages: readonly string[] = []): Promise<void> {
  const root = repositoryRoot();
  const matrixFiles = (await import("node:fs/promises").then(({readdir}) => readdir(join(root, "benchmark-matrices")))).filter((name) => name.endsWith(".json"));
  const matrices = new Map<string, BenchmarkMatrix>();
  for (const file of matrixFiles) { const matrix = await loadMatrix(join(root, "benchmark-matrices", file)); matrices.set(matrix.id, matrix); }
  const grouped = new Map<string, Readonly<Record<string, unknown>>[]>();
  for (const result of results) { const key = JSON.stringify([result["benchmark_matrix_id"], result["language"]]); const selected = grouped.get(key) ?? []; selected.push(result); grouped.set(key, selected); }
  const matrixIds = new Set(results.map((result) => String(result["benchmark_matrix_id"])));
  const languages = requiredLanguages.length > 0 ? requiredLanguages : [...new Set(results.map((result) => String(result["language"])))];
  for (const matrixId of matrixIds) for (const language of languages) {
    const selected = grouped.get(JSON.stringify([matrixId, language])) ?? [];
    const matrix = matrices.get(matrixId);
    if (matrix === undefined) throw new Error(`cannot verify completeness of unknown matrix ${matrixId}`);
    const expected = new Set<string>();
    for (const solver of matrix.solvers) for (const resolution of matrix.resolutions) for (let repetition = 1; repetition <= matrix.repetitions; repetition += 1) expected.add(JSON.stringify([solver, resolution, repetition]));
    const observedValues = selected.map((result) => JSON.stringify([result["solver"], result["resolution"], result["repetition"]]));
    const observed = new Set(observedValues);
    if (observed.size !== observedValues.length) throw new Error(`duplicate ${language} artifacts for matrix ${matrixId}`);
    const missing = [...expected].filter((key) => !observed.has(key)); const extra = [...observed].filter((key) => !expected.has(key));
    const failed = selected.filter((result) => result["success"] !== true).length;
    if (missing.length > 0 || extra.length > 0 || failed > 0) throw new Error(`incomplete ${language} artifacts for matrix ${matrixId}: missing=${JSON.stringify(missing)} extra=${JSON.stringify(extra)} failed=${String(failed)}`);
  }
}

function assertRequiredLanguages(results: readonly Readonly<Record<string, unknown>>[], requiredLanguages: readonly string[]): void {
  const expected = new Set(requiredLanguages);
  if (expected.size === 0 || expected.size !== requiredLanguages.length) throw new Error("required languages must be a non-empty unique roster");
  const observed = new Set(results.map((result) => String(result["language"])));
  const missing = [...expected].filter((language) => !observed.has(language)).sort();
  const extra = [...observed].filter((language) => !expected.has(language)).sort();
  if (missing.length > 0 || extra.length > 0) throw new Error(`benchmark producer roster mismatch: missing=${JSON.stringify(missing)} extra=${JSON.stringify(extra)}`);
}

export async function compareResults(directory: string, requireComplete = false, requiredLanguages: readonly string[] = []): Promise<string> {
  const root = repositoryRoot();
  const selected = isAbsolute(directory) ? directory : join(root, directory);
  const files = await import("node:fs/promises").then(({readdir}) => readdir(selected, {recursive: true}));
  const schema = await json(join(root, "spec/result.schema.json")) as object;
  const validator = new Ajv2020({strict: true, allErrors: true}).compile(schema);
  const signatures = new Map<string, unknown>();
  const results: Readonly<Record<string, unknown>>[] = [];
  const lines = ["language    solver              median ms      p95 ms    sim/wall success"];
  for (const file of files) {
    if (!file.endsWith(".json")) continue;
    const value = await json(join(selected, file)) as Record<string, unknown>;
    const solver = value["solver"];
    if (typeof solver !== "string" || typeof value["benchmark_matrix_id"] !== "string" || typeof value["success"] !== "boolean") continue;
    if (!validator(value)) throw new TypeError(new Ajv2020().errorsText(validator.errors));
    validateResultSemantics(value);
    results.push(value);
    const key = JSON.stringify([value["benchmark_matrix_id"], value["scenario_id"], value["precision"], value["resolution"], value["solver"]]);
    const signature = physicalIdentity(value);
    const previous = signatures.get(key);
    if (previous !== undefined && !physicalIdentitiesMatch(previous, signature, value["precision"])) throw new Error("benchmark artifacts reuse a matrix/scenario/resolution identity with different physical inputs");
    signatures.set(key, signature);
    const language = typeof value["language"] === "string" ? value["language"] : "unknown";
    const median = (Number(value["median_step_seconds"]) * 1000).toFixed(3);
    const p95 = (Number(value["p95_step_seconds"]) * 1000).toFixed(3);
    const throughput = Number(value["simulated_seconds_per_wall_second"]).toFixed(3);
    lines.push(`${language.padEnd(12)}${solver.padEnd(20)}${median.padStart(10)}${p95.padStart(12)}${throughput.padStart(12)} ${String(value["success"])}`);
  }
  if (results.length === 0 && (requireComplete || requiredLanguages.length > 0)) throw new Error("strict benchmark comparison found no result artifacts");
  if (requiredLanguages.length > 0) assertRequiredLanguages(results, requiredLanguages);
  if (requireComplete) await assertCompleteMatrices(results, requiredLanguages);
  return lines.join("\n");
}
