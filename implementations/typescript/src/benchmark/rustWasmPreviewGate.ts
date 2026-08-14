import {chromium} from "playwright";
import {readFile} from "node:fs/promises";
import {resolve} from "node:path";
import {createServer} from "vite";
import {parseScenario} from "../core/scenario.js";
import type {BrowserRunRequest, BrowserRunResult} from "./types.js";

const root = resolve(import.meta.dirname, "../../../..");
const implementationRoot = resolve(root, "implementations/typescript");
const scenarioDocument = JSON.parse(await readFile(resolve(root, "scenarios/airfoil/default.json"), "utf8")) as unknown;
const schema = JSON.parse(await readFile(resolve(root, "spec/schemas/scenario.schema.json"), "utf8")) as object;
const base = parseScenario(scenarioDocument, schema);
const scenario = {...base, domain: {...base.domain, resolution: [160, 96] as const}};
const server = await createServer({root: implementationRoot, server: {host: "127.0.0.1", port: 0}});
await server.listen();
const address = server.httpServer?.address();
if (address === null || address === undefined || typeof address === "string") throw new Error("preview server did not bind");
const browser = await chromium.launch({headless: true});
const results: string[] = [];

function failureDetails(result: BrowserRunResult): string {
  const failure = result.failure;
  const classification = failure === null
    ? "no classified failure"
    : `kind=${failure.kind} reason=${failure.reason ?? "none"} stage=${failure.stage ?? "none"} message=${failure.message}`;
  const warnings = result.warnings.length === 0 ? "none" : JSON.stringify(result.warnings);
  return `${classification}; warnings=${warnings}; completed_steps=${String(result.stepSeconds.length)}`;
}

try {
  const page = await browser.newPage();
  await page.goto(`http://127.0.0.1:${String(address.port)}/benchmark.html`);
  for (const solverId of ["stable-fluids", "lbm-d2q9", "pic-flip"] as const) {
    const request: BrowserRunRequest = {scenario, solverId, duration: 8 * scenario.outputDt, backend: "rust-wasm"};
    const response = await page.evaluate(async (value) => {
      const host = globalThis as unknown as {runFoilBench(request: BrowserRunRequest): Promise<{result: BrowserRunResult}>};
      return host.runFoilBench(value);
    }, request);
    if (!response.result.success || response.result.stepSeconds.length === 0) {
      throw new Error(`${solverId} Rust/WASM preview failed: ${failureDetails(response.result)}`);
    }
    const sorted = [...response.result.stepSeconds].sort((left, right) => left - right);
    const median = sorted[Math.floor(sorted.length / 2)] ?? Number.POSITIVE_INFINITY;
    const rate = 1 / median;
    if (rate < 10) throw new Error(`${solverId} Rust/WASM preview rate ${rate.toFixed(2)}/s is below 10/s`);
    results.push(`${solverId}=${rate.toFixed(2)}/s`);
  }
} finally {
  await browser.close();
  await server.close();
}
console.log(`Rust/WASM 160x96 preview passed: ${results.join(", ")}`);
