import {readFile} from "node:fs/promises";
import {resolve} from "node:path";
import {describe, expect, it} from "vitest";
import {controlAt, parseScenario, validateDocument} from "../../src/core/scenario.js";
import {runChaoticWakeCase, runChaosSensitivity} from "../../src/experiments/chaoticWake.js";
import {createSolver} from "../../src/solvers/factory.js";

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

  it("sustains the full-resolution accepted startup through internal stability retries", async () => {
    const root = resolve("../..");
    const schema = JSON.parse(await readFile(resolve(root, "spec/scenario.schema.json"), "utf8")) as object;
    const fixture = JSON.parse(await readFile(resolve(root, "spec/conformance/solver-validity.json"), "utf8")) as {stable_retry_case: {scenario: string; duration: number; expected_steps: number; minimum_total_stability_retries: number}};
    const scenario = parseScenario(JSON.parse(await readFile(resolve(root, fixture.stable_retry_case.scenario), "utf8")) as unknown, schema);
    const solver = createSolver("stable-fluids");
    solver.initialize(scenario, scenario.seed);
    let totalRetries = 0;
    for (let step = 1; step <= fixture.stable_retry_case.expected_steps; step += 1) {
      const report = solver.advance(controlAt(scenario, step * scenario.outputDt), scenario.outputDt);
      expect(report.stateRevision).toBe(step);
      totalRetries += Number(report.evidence["stability_retries"] ?? 0);
    }
    expect(solver.exportState().time).toBeCloseTo(fixture.stable_retry_case.duration, 6);
    expect(totalRetries).toBeGreaterThanOrEqual(fixture.stable_retry_case.minimum_total_stability_retries);
    expect(solver.exportState().velocity.every(Number.isFinite)).toBe(true);
  }, 15_000);
});
