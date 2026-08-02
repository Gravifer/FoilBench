#!/usr/bin/env node
import {readFile} from "node:fs/promises";
import {dirname, join, resolve} from "node:path";
import {fileURLToPath} from "node:url";
import {createServer} from "vite";
import {compareResults, runBrowserMatrix} from "./benchmark/runner.js";
import {parseScenario} from "./core/scenario.js";

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
    const server = await createServer({root: join(repositoryRoot, "implementations/typescript"), server: {host: "127.0.0.1", port: 4173, strictPort: true}});
    await server.listen();
    server.printUrls();
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
  throw new Error(`command ${command} is not implemented yet`);
}

await main(process.argv.slice(2));
