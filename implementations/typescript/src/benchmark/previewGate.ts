import {readFile, readdir} from "node:fs/promises";
import {dirname, join, resolve} from "node:path";
import {fileURLToPath} from "node:url";
import {runBrowserMatrix} from "./runner.js";

const repositoryRoot = resolve(dirname(fileURLToPath(import.meta.url)), "../../../..");
const destination = await runBrowserMatrix(join(repositoryRoot, "benchmark-matrices/preview-gate.json"));
const files = (await readdir(destination)).filter((name) => name.endsWith(".json"));
const failures: string[] = [];
for (const file of files) {
  const result = JSON.parse(await readFile(join(destination, file), "utf8")) as Record<string, unknown>;
  const median = Number(result["median_step_seconds"]);
  const success = result["success"] === true;
  if (!success || !Number.isFinite(median) || median >= 0.1) failures.push(`${file}: median=${String(median)} success=${String(success)}`);
}
if (files.length !== 9) failures.push(`expected 9 result artifacts, found ${String(files.length)}`);
if (failures.length > 0) throw new Error(`TypeScript 160x96 preview gate failed:\n${failures.join("\n")}`);
console.log(`TypeScript 160x96 preview gate passed: ${destination}`);
