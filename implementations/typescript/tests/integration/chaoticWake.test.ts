import {readFile} from "node:fs/promises";
import {resolve} from "node:path";
import {describe, expect, it} from "vitest";
import {parseScenario, validateDocument} from "../../src/core/scenario.js";
import {runChaoticWakeCase, runChaosSensitivity} from "../../src/experiments/chaoticWake.js";
import {createSolver} from "../../src/solvers/factory.js";

describe("TypeScript chaotic-wake parity", () => {
  it("emits deterministic schema-valid sweep and sensitivity envelopes", async () => {
    const root = resolve("../..");
    const scenario = parseScenario(
      JSON.parse(await readFile(resolve(root, "scenarios/airfoil/chaotic-experimental.json"), "utf8")) as unknown,
      JSON.parse(await readFile(resolve(root, "spec/schemas/scenario.schema.json"), "utf8")) as object,
    );
    const resultSchema = JSON.parse(
      await readFile(
        resolve(
          root,
          "spec/schemas/chaotic-wake-result-v2.schema.json",
        ),
        "utf8",
      ),
    ) as object;
    const selected = {reynolds: 1000, angleDegrees: 25, resolution: [24, 16] as const};
    const first = runChaoticWakeCase(scenario, selected, 0.1, 0.02); const second = runChaoticWakeCase(scenario, selected, 0.1, 0.02);
    validateDocument(first, resultSchema); validateDocument(second, resultSchema);
    expect(first.metrics).toEqual(second.metrics);
    expect(Object.values(first.metrics).every(Number.isFinite)).toBe(true);
    const sensitivity = runChaosSensitivity(scenario, selected, 0.08, 1e-4);
    validateDocument(sensitivity, resultSchema);
    expect(Object.values(sensitivity.metrics).every(Number.isFinite)).toBe(true);
    expect(Number(sensitivity.metrics["initial_wake_rms_difference"])).toBeGreaterThan(2e-6);
    expect(Number(sensitivity.metrics["initial_wake_rms_difference"])).toBeLessThan(2e-5);
    expect(sensitivity.initialization).toMatchObject({
      reference_import_status: "accepted",
      perturbed_import_status: "accepted",
      authoritative_angle_degrees: 25,
      requested_epsilon: 1e-4,
    });
    expect(sensitivity.series?.times.length).toBeGreaterThan(0);
  });

  it("sustains the full-resolution accepted startup through internal stability retries", async () => {
    const root = resolve("../..");
    const schema = JSON.parse(await readFile(resolve(root, "spec/schemas/scenario.schema.json"), "utf8")) as object;
    const fixture = JSON.parse(await readFile(resolve(root, "spec/conformance/solver-validity.json"), "utf8")) as {planning_retry_cases: {"stable-fluids": {scenario: string; resolution: [number, number]; target_dt: number; angle_degrees: number; angular_velocity_degrees: number; solver_options: {stable_cfl: number}; expected_steps: number; minimum_total_stability_retries: number}}};
    const retryCase = fixture.planning_retry_cases["stable-fluids"];
    const loaded = parseScenario(JSON.parse(await readFile(resolve(root, retryCase.scenario), "utf8")) as unknown, schema);
    const scenario = {...loaded, domain: {...loaded.domain, resolution: retryCase.resolution}, outputDt: retryCase.target_dt, solverOptions: {...loaded.solverOptions, stableCfl: retryCase.solver_options.stable_cfl}};
    const solver = createSolver("stable-fluids");
    solver.initialize(scenario, scenario.seed);
    let totalRetries = 0;
    expect(retryCase.expected_steps).toBeGreaterThanOrEqual(1);
    for (let step = 1; step <= retryCase.expected_steps; step += 1) {
      const report = solver.advance({time: step * scenario.outputDt, angleDegrees: retryCase.angle_degrees, angularVelocityDegrees: retryCase.angular_velocity_degrees}, scenario.outputDt);
      expect(report.stateRevision).toBe(step);
      totalRetries += Number(report.evidence["stability_retries"] ?? 0);
    }
    expect(solver.stateRevision).toBe(retryCase.expected_steps);
    expect(solver.exportState().time).toBeCloseTo(retryCase.expected_steps * scenario.outputDt, 6);
    expect(totalRetries).toBeGreaterThanOrEqual(retryCase.minimum_total_stability_retries);
    expect(solver.exportState().velocity.every(Number.isFinite)).toBe(true);
  }, 45_000);

  it("starts full-resolution PIC/FLIP with bounded planning evidence", async () => {
    const root = resolve("../..");
    const schema = JSON.parse(await readFile(resolve(root, "spec/schemas/scenario.schema.json"), "utf8")) as object;
    const fixture = JSON.parse(await readFile(resolve(root, "spec/conformance/solver-validity.json"), "utf8")) as {planning_retry_cases: {"pic-flip": {scenario: string; resolution: [number, number]; target_dt: number; angle_degrees: number; angular_velocity_degrees: number; solver_options: {pic_cfl: number}; expected_steps: number; minimum_total_stability_retries: number}}};
    const retryCase = fixture.planning_retry_cases["pic-flip"];
    expect(retryCase.expected_steps).toBeGreaterThanOrEqual(1);
    const loaded = parseScenario(JSON.parse(await readFile(resolve(root, retryCase.scenario), "utf8")) as unknown, schema);
    const scenario = {...loaded, domain: {...loaded.domain, resolution: retryCase.resolution}, outputDt: retryCase.target_dt, solverOptions: {...loaded.solverOptions, picCfl: retryCase.solver_options.pic_cfl}};
    const solver = createSolver("pic-flip"); solver.initialize(scenario, scenario.seed);
    let totalRetries = 0; let report = null as ReturnType<typeof solver.advance> | null;
    for (let step = 1; step <= retryCase.expected_steps; step += 1) { report = solver.advance({time: step * scenario.outputDt, angleDegrees: retryCase.angle_degrees, angularVelocityDegrees: retryCase.angular_velocity_degrees}, scenario.outputDt); totalRetries += Number(report.evidence["stability_retries"]); }
    expect(solver.stateRevision).toBe(retryCase.expected_steps); expect(report).not.toBeNull();
    if (report === null) throw new Error("planning retry fixture executed no PIC/FLIP steps");
    expect(totalRetries).toBeGreaterThanOrEqual(retryCase.minimum_total_stability_retries);
    expect(Number(report.evidence["maximum_particle_cfl"])).toBeLessThanOrEqual(retryCase.solver_options.pic_cfl * (1 + 1e-6));
    expect(solver.exportState().velocity.every(Number.isFinite)).toBe(true);
  }, 15_000);

  it("retries full-resolution LBM when its first Mach plan is stale", async () => {
    const root = resolve("../.."); const schema = JSON.parse(await readFile(resolve(root, "spec/schemas/scenario.schema.json"), "utf8")) as object;
    const fixture = JSON.parse(await readFile(resolve(root, "spec/conformance/solver-validity.json"), "utf8")) as {planning_retry_cases: {"lbm-d2q9": {scenario: string; angle_degrees: number; expected_steps: number; minimum_total_stability_retries: number}}};
    const retryCase = fixture.planning_retry_cases["lbm-d2q9"]; const scenario = parseScenario(JSON.parse(await readFile(resolve(root, retryCase.scenario), "utf8")) as unknown, schema); const solver = createSolver("lbm-d2q9");
    expect(retryCase.expected_steps).toBeGreaterThanOrEqual(1);
    solver.restart(scenario, scenario.seed, {time: 0, angleDegrees: retryCase.angle_degrees, reynolds: scenario.reynolds});
    let totalRetries = 0; let report = null as ReturnType<typeof solver.advance> | null;
    for (let step = 1; step <= retryCase.expected_steps; step += 1) { report = solver.advance({time: step * scenario.outputDt, angleDegrees: retryCase.angle_degrees, angularVelocityDegrees: 0}, scenario.outputDt); totalRetries += Number(report.evidence["stability_retries"]); }
    expect(solver.stateRevision).toBe(retryCase.expected_steps); expect(report).not.toBeNull();
    if (report === null) throw new Error("planning retry fixture executed no LBM steps");
    expect(totalRetries).toBeGreaterThanOrEqual(retryCase.minimum_total_stability_retries); expect(Number(report.evidence["maximum_lattice_mach"])).toBeLessThanOrEqual(0.08 * (1 + 1e-6));
  }, 15_000);
});
