import {readFile} from "node:fs/promises";
import {dirname, join, resolve} from "node:path";
import {fileURLToPath} from "node:url";
import type {SolverId} from "../core/contracts.js";
import {controlAt, parseScenario} from "../core/scenario.js";
import {createSolver} from "../solvers/factory.js";

const root = resolve(dirname(fileURLToPath(import.meta.url)), "../../../..");
const fixture = JSON.parse(await readFile(join(root, "spec/conformance/fullsize-acceptance.json"), "utf8")) as {startup: {scenario: string; resolution: readonly [number, number]; steps: number; solvers: readonly SolverId[]}};
const schema = JSON.parse(await readFile(join(root, "spec/scenario.schema.json"), "utf8")) as object;
const gate = fixture.startup;
const scenario = parseScenario(JSON.parse(await readFile(join(root, gate.scenario), "utf8")) as unknown, schema);
if (scenario.domain.resolution[0] !== gate.resolution[0] || scenario.domain.resolution[1] !== gate.resolution[1]) throw new Error("startup fixture resolution disagrees with its scenario");
if (!Number.isInteger(gate.steps) || gate.steps < 1) throw new Error("startup gate requires at least one step");
for (const solverId of gate.solvers) {
  const solver = createSolver(solverId);
  solver.initialize(scenario, scenario.seed);
  for (let step = 1; step <= gate.steps; step += 1) {
    const targetTime = step * scenario.outputDt;
    const report = solver.advance(controlAt(scenario, targetTime), scenario.outputDt);
    if (Math.abs(report.advancedDt - scenario.outputDt) > 1e-8) throw new Error(`${solverId} violated requested startup time`);
  }
  const state = solver.exportState();
  if (!state.velocity.every(Number.isFinite)) throw new Error(`${solverId} produced a non-finite startup state`);
  if (solver.diagnostics().stateRevision !== solver.stateRevision) throw new Error(`${solverId} produced stale startup diagnostics`);
  console.log(`${solverId.padEnd(18)}passed ${String(gate.steps)} startup step(s)`);
}
