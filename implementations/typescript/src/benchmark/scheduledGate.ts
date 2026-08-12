import {readFile} from "node:fs/promises";
import {dirname, join, resolve} from "node:path";
import {fileURLToPath} from "node:url";
import type {SolverId} from "../core/contracts.js";
import {controlAt, parseScenario} from "../core/scenario.js";
import {createSolver} from "../solvers/factory.js";

const root = resolve(dirname(fileURLToPath(import.meta.url)), "../../../..");
const fixture = JSON.parse(await readFile(join(root, "spec/conformance/fullsize-acceptance.json"), "utf8")) as {scheduled_checkpoints: {scenario: string; solver: SolverId; resolution: readonly [number, number]; times: readonly number[]}};
const schema = JSON.parse(await readFile(join(root, "spec/schemas/scenario.schema.json"), "utf8")) as object;
const gate = fixture.scheduled_checkpoints;
const scenario = parseScenario(JSON.parse(await readFile(join(root, gate.scenario), "utf8")) as unknown, schema);
if (scenario.domain.resolution[0] !== gate.resolution[0] || scenario.domain.resolution[1] !== gate.resolution[1]) throw new Error("scheduled fixture resolution disagrees with its scenario");
const solver = createSolver(gate.solver); solver.initialize(scenario, scenario.seed);
const checkpointSteps = new Map(gate.times.map((time) => [Math.round(time / scenario.outputDt), time])); const lastStep = Math.max(...checkpointSteps.keys());
for (let step = 1; step <= lastStep; step += 1) {
  const time = step * scenario.outputDt; solver.advance(controlAt(scenario, time), scenario.outputDt);
  if (checkpointSteps.has(step)) { if (!solver.exportState().velocity.every(Number.isFinite)) throw new Error(`non-finite scheduled state at t=${String(time)}`); console.log(`passed scheduled checkpoint t=${time.toFixed(2)}`); }
}
