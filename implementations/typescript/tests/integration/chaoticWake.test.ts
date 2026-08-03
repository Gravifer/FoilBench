import {readFile} from "node:fs/promises";
import {resolve} from "node:path";
import {describe, expect, it} from "vitest";
import {parseScenario, validateDocument} from "../../src/core/scenario.js";
import {runChaoticWakeCase, runChaosSensitivity} from "../../src/experiments/chaoticWake.js";

describe("TypeScript chaotic-wake parity", () => {
  it("emits deterministic schema-valid sweep and sensitivity envelopes", async () => {
    const root = resolve("../..");
    const scenario = parseScenario(
      JSON.parse(await readFile(resolve(root, "scenarios/airfoil/chaotic-experimental.json"), "utf8")) as unknown,
      JSON.parse(await readFile(resolve(root, "spec/scenario.schema.json"), "utf8")) as object,
    );
    const resultSchema = JSON.parse(await readFile(resolve(root, "spec/chaotic-wake-result.schema.json"), "utf8")) as object;
    const selected = {reynolds: 1000, angleDegrees: 25, resolution: [24, 16] as const};
    const first = runChaoticWakeCase(scenario, selected, 0.1, 0.02); const second = runChaoticWakeCase(scenario, selected, 0.1, 0.02);
    validateDocument(first, resultSchema); validateDocument(second, resultSchema);
    expect(first.metrics).toEqual(second.metrics);
    expect(Object.values(first.metrics).every(Number.isFinite)).toBe(true);
    const sensitivity = runChaosSensitivity(scenario, selected, 0.08, 1e-4);
    validateDocument(sensitivity, resultSchema);
    expect(Object.values(sensitivity.metrics).every(Number.isFinite)).toBe(true);
    expect(sensitivity.series?.times.length).toBeGreaterThan(0);
  });
});
