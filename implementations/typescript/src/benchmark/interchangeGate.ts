import {readFile, readdir} from "node:fs/promises";
import {dirname, join, resolve} from "node:path";
import {fileURLToPath} from "node:url";
import type {SolverId} from "../core/contracts.js";
import {parseScenario} from "../core/scenario.js";
import {createSolver} from "../solvers/factory.js";
import {loadCanonicalSnapshot} from "./runner.js";

const repositoryRoot = resolve(dirname(fileURLToPath(import.meta.url)), "../../../..");
const resultsRoot = resolve(process.argv[2] ?? "results");
const schema = JSON.parse(await readFile(join(repositoryRoot, "spec/schemas/scenario.schema.json"), "utf8")) as object;
const base = parseScenario(JSON.parse(await readFile(join(repositoryRoot, "scenarios/airfoil/default.json"), "utf8")) as unknown, schema);
const solverIds: readonly SolverId[] = ["stable-fluids", "lbm-d2q9", "pic-flip"];
const expected = new Set(["python", "julia", "typescript"].flatMap((language) => solverIds.map((solver) => `${language}/${solver}`)));
const observed = new Set<string>();

for (const relative of await readdir(resultsRoot, {recursive: true})) {
  if (!relative.endsWith("manifest.json")) continue;
  const state = await loadCanonicalSnapshot(dirname(join(resultsRoot, relative)));
  const source = `${state.sourceLanguage}/${state.sourceSolver}`;
  if (observed.has(source)) throw new Error(`duplicate canonical snapshot from ${source}`);
  observed.add(source);
  const scenario = {...base, domain: {...base.domain, resolution: state.resolution}};
  const control = {time: state.time, angleDegrees: state.angleDegrees, angularVelocityDegrees: state.angularVelocityDegrees};
  for (const destinationId of solverIds) {
    const destination = createSolver(destinationId); destination.initialize(scenario, scenario.seed);
    const outcome = destination.importState(state, control);
    if (outcome.status !== "accepted") throw new Error(`TypeScript rejected ${source} in ${destinationId}: ${outcome.reason} at ${outcome.stage ?? "canonical-import"}`);
  }
}
const missing = [...expected].filter((value) => !observed.has(value));
const extra = [...observed].filter((value) => !expected.has(value));
if (missing.length > 0 || extra.length > 0) throw new Error(`canonical producer roster mismatch: missing=${JSON.stringify(missing)} extra=${JSON.stringify(extra)}`);
console.log("TypeScript imported all 27 cross-language canonical conversions");
