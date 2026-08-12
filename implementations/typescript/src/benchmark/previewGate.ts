import {readFile, readdir} from "node:fs/promises";
import {dirname, join, resolve} from "node:path";
import {fileURLToPath} from "node:url";
import {loadMatrix, runBrowserMatrix} from "./runner.js";

const repositoryRoot = resolve(dirname(fileURLToPath(import.meta.url)), "../../../..");
const fixture = JSON.parse(await readFile(join(repositoryRoot, "spec/conformance/fullsize-acceptance.json"), "utf8")) as {preview: {minimum_warmed_solver_steps_per_second: number; solvers: readonly string[]}};
const matrixPath = join(repositoryRoot, "benchmark-matrices/preview-gate.json");
const matrix = await loadMatrix(matrixPath);
const destination = await runBrowserMatrix(matrixPath);
const files = (await readdir(destination)).filter((name) => name.endsWith(".json"));
const failures: string[] = [];
for (const file of files) {
  const result = JSON.parse(await readFile(join(destination, file), "utf8")) as Record<string, unknown>;
  const median = Number(result["median_step_seconds"]);
  const success = result["success"] === true;
  const minimumRate = fixture.preview.minimum_warmed_solver_steps_per_second;
  if (!success || !Number.isFinite(median) || median > 1 / minimumRate) failures.push(`${file}: median=${String(median)} success=${String(success)}`);
}
const expected = new Set<string>();
for (const resolution of matrix.resolutions) for (const solver of matrix.solvers) for (let repetition = 1; repetition <= matrix.repetitions; repetition += 1) expected.add(`${solver}-${String(resolution[0])}x${String(resolution[1])}-r${String(repetition)}.json`);
const observed = new Set(files);
for (const file of expected) if (!observed.has(file)) failures.push(`missing ${file}`);
for (const file of observed) if (!expected.has(file)) failures.push(`unexpected ${file}`);
const matrixSolvers = new Set<string>(matrix.solvers);
if (matrixSolvers.size !== fixture.preview.solvers.length || fixture.preview.solvers.some((solver) => !matrixSolvers.has(solver))) failures.push("preview matrix solver roster disagrees with acceptance fixture");
if (failures.length > 0) throw new Error(`TypeScript 160x96 preview gate failed:\n${failures.join("\n")}`);
console.log(`TypeScript 160x96 preview gate passed: ${destination}`);
