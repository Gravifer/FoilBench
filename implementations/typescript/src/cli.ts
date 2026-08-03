#!/usr/bin/env node
import {readFile, writeFile} from "node:fs/promises";
import {dirname, join, resolve} from "node:path";
import {fileURLToPath} from "node:url";
import {createServer} from "vite";
import {compareResults, runBrowserMatrix} from "./benchmark/runner.js";
import type {SolverId} from "./core/contracts.js";
import {parseScenario} from "./core/scenario.js";
import {validateDocument} from "./core/scenario.js";
import {runChaoticWakeCase, runChaosSensitivity} from "./experiments/chaoticWake.js";
import type {ExperimentEnvelope, WakeCase} from "./experiments/chaoticWake.js";

const solvers = [
  {id: "stable-fluids", display_name: "Stable Fluids (MAC)", dimensions: [2]},
  {id: "lbm-d2q9", display_name: "D2Q9 TRT LBM", dimensions: [2]},
  {id: "pic-flip", display_name: "Blended PIC/FLIP", dimensions: [2]},
] as const;
const repositoryRoot = resolve(dirname(fileURLToPath(import.meta.url)), "../../..");

async function main(args: readonly string[]): Promise<void> {
  const command = args[0] ?? "describe";
  if (command === "describe") {
    console.log(JSON.stringify({implementation: "typescript", version: "0.1.0", canonical_reference: false, thin_3d: false, solvers}, null, 2));
    return;
  }
  if (command === "scenario") {
    const path = resolve(repositoryRoot, args[1] ?? "scenarios/airfoil/default.json");
    const schemaPath = join(repositoryRoot, "spec/scenario.schema.json");
    const scenario = parseScenario(JSON.parse(await readFile(path, "utf8")) as unknown, JSON.parse(await readFile(schemaPath, "utf8")) as object);
    console.log(JSON.stringify(scenario, null, 2));
    return;
  }
  if (command === "view") {
    const scenarioPath = resolve(repositoryRoot, args[1] ?? "scenarios/airfoil/default.json");
    const solverFlag = args.indexOf("--solver");
    const solverId = (solverFlag >= 0 ? args[solverFlag + 1] : "stable-fluids") as SolverId;
    if (!solvers.some((solver) => solver.id === solverId)) throw new RangeError(`unknown solver: ${solverId}`);
    const server = await createServer({root: join(repositoryRoot, "implementations/typescript"), server: {host: "127.0.0.1", port: 4173, strictPort: true}});
    await server.listen();
    const parameters = new URLSearchParams({scenario: `/@fs/${scenarioPath.replaceAll("\\", "/")}`, solver: solverId});
    console.log(`FoilBench TypeScript viewer: http://127.0.0.1:4173/?${parameters.toString()}`);
    return;
  }
  if (command === "bench") {
    console.log(await runBrowserMatrix(args[1] ?? "benchmark-matrices/smoke.json", args[2]));
    return;
  }
  if (command === "compare") {
    console.log(await compareResults(args[1] ?? "results/typescript"));
    return;
  }
  if (command === "chaos-sweep" || command === "chaos-paired") {
    const casesDocument = JSON.parse(await readFile(join(repositoryRoot, "spec/conformance/chaotic-wake-cases.json"), "utf8")) as {
      scenario: string;
      sweep: {duration: number; burn_in: number; cases: readonly {reynolds: number; angle_degrees: number; resolution: readonly [number, number]}[]};
      sensitivity: {duration: number; epsilon: number; case: {reynolds: number; angle_degrees: number; resolution: readonly [number, number]}};
    };
    const scenarioPath = resolve(repositoryRoot, args[1] ?? casesDocument.scenario);
    const scenario = parseScenario(JSON.parse(await readFile(scenarioPath, "utf8")) as unknown, JSON.parse(await readFile(join(repositoryRoot, "spec/scenario.schema.json"), "utf8")) as object);
    const selected = (value: {reynolds: number; angle_degrees: number; resolution: readonly [number, number]}): WakeCase => ({reynolds: value.reynolds, angleDegrees: value.angle_degrees, resolution: value.resolution});
    const results: readonly ExperimentEnvelope[] = command === "chaos-sweep"
      ? casesDocument.sweep.cases.map((value) => runChaoticWakeCase(scenario, selected(value), casesDocument.sweep.duration, casesDocument.sweep.burn_in))
      : [runChaosSensitivity(scenario, selected(casesDocument.sensitivity.case), casesDocument.sensitivity.duration, casesDocument.sensitivity.epsilon)];
    const resultSchema = JSON.parse(await readFile(join(repositoryRoot, "spec/chaotic-wake-result.schema.json"), "utf8")) as object;
    for (const result of results) validateDocument(result, resultSchema);
    const text = JSON.stringify(command === "chaos-sweep" ? results : results[0], null, 2);
    if (args[2] !== undefined) await writeFile(resolve(repositoryRoot, args[2]), text, "utf8");
    console.log(text);
    return;
  }
  throw new Error(`command ${command} is not implemented yet`);
}

await main(process.argv.slice(2));
