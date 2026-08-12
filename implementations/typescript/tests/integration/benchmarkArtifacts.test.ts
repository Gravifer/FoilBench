import {mkdtemp, rm, writeFile} from "node:fs/promises";
import {tmpdir} from "node:os";
import {join} from "node:path";
import {afterEach, describe, expect, it} from "vitest";
import {assertCompleteMatrices, compareResults, validateResultSemantics} from "../../src/benchmark/runner.js";

function result(language: string): Record<string, unknown> {
  return {
    schema_version: 1,
    contract_id: "foilbench-phase2-v1",
    contract_revision: 4,
    benchmark_matrix_id: "test",
    scenario_id: "default-airfoil",
    repetition: 1,
    language,
    solver: "stable-fluids",
    git_commit: "test",
    machine: {},
    precision: "float32",
    resolution: [32, 16],
    bounds: [[-2, 6], [-2, 2]],
    periodic_axes: [],
    reynolds: 500,
    effective_reynolds: 500,
    solver_configuration: {initial_condition: "freestream", stable_advection: "maccormack", stable_face_advection: false, stable_cfl: 0.7, pressure_tolerance: 1e-5, pressure_max_iterations: 640, pic_flip_blend: 0.95, pic_population_interval: 8, pic_cfl: 0.75},
    freestream: [1, 0],
    foil: {naca: "2412", chord: 1, pivot: [0, 0]},
    control_history: [{time: 0, angle_degrees: 4}],
    requested_duration: 0.1,
    simulated_duration: 0.1,
    output_dt: 0.01,
    seed: 0,
    initialization_seconds: 0.01,
    cold_step_seconds: 0.02,
    step_seconds: [0.01],
    median_step_seconds: 0.01,
    p95_step_seconds: 0.01,
    simulated_seconds_per_wall_second: 10,
    cell_updates_per_second: 51200,
    particle_updates_per_second: 0,
    peak_rss_bytes: null,
    memory_measurement: "unavailable",
    runtime_startup_seconds: 0.1,
    worker_startup_seconds: 0.02,
    substeps: 1,
    final_state_revision: 1,
    diagnostic_state_revision: 1,
    last_step: {requested_dt: 0.01, advanced_dt: 0.01, substeps: 1, max_speed: 1, state_revision: 1, evidence: {maximum_fluid_speed: 1}, warnings: []},
    diagnostics: {energy: 0.5},
    success: true,
    failure: null,
    warnings: [],
  };
}

describe("benchmark artifact comparison", () => {
  const directories: string[] = [];

  afterEach(async () => {
    await Promise.all(directories.splice(0).map(async (directory) => rm(directory, {recursive: true, force: true})));
  });

  it("validates results and rejects mismatched physical identities", async () => {
    const directory = await mkdtemp(join(tmpdir(), "foilbench-ts-results-"));
    directories.push(directory);
    const typescript = result("typescript");
    await writeFile(join(directory, "typescript.json"), JSON.stringify(typescript), "utf8");
    expect(await compareResults(directory)).toContain("stable-fluids");

    const mismatched = result("python");
    mismatched["reynolds"] = 1000;
    await writeFile(join(directory, "python.json"), JSON.stringify(mismatched), "utf8");
    await expect(compareResults(directory)).rejects.toThrow("different physical inputs");
  });

  it("rejects an incomplete known matrix in strict comparison mode", async () => {
    const directory = await mkdtemp(join(tmpdir(), "foilbench-ts-incomplete-"));
    directories.push(directory);
    await writeFile(join(directory, "stable.json"), JSON.stringify(result("typescript")), "utf8");
    await expect(compareResults(directory, true)).rejects.toThrow("incomplete typescript artifacts");
    for (const solver of ["lbm-d2q9", "pic-flip"]) { const artifact = result("typescript"); artifact["solver"] = solver; await writeFile(join(directory, `${solver}.json`), JSON.stringify(artifact), "utf8"); }
    await expect(compareResults(directory, true)).resolves.toContain("stable-fluids");
    await expect(compareResults(directory, false, ["typescript"])).resolves.toContain("stable-fluids");
    await expect(compareResults(directory, false, ["python", "typescript"])).rejects.toThrow("producer roster mismatch");
  });

  it("uses semantic numeric equality and key-order-independent identity", async () => {
    const directory = await mkdtemp(join(tmpdir(), "foilbench-ts-equivalent-"));
    directories.push(directory);
    const first = result("typescript");
    const second = result("python");
    second["foil"] = {pivot: [0, 0], chord: 1.0, naca: "2412"};
    second["output_dt"] = 0.010000005;
    second["effective_reynolds"] = 499.99975;
    await writeFile(join(directory, "first.json"), JSON.stringify(first), "utf8");
    await writeFile(join(directory, "second.json"), JSON.stringify(second), "utf8");
    await expect(compareResults(directory)).resolves.toContain("python");
    second["output_dt"] = 0.011;
    await writeFile(join(directory, "second.json"), JSON.stringify(second), "utf8");
    await expect(compareResults(directory)).rejects.toThrow("different physical inputs");
  });

  it("rejects contradictory successful artifacts", () => {
    const contradictory = result("typescript");
    contradictory["failure"] = {kind: "unexpected", reason: null, stage: null, message: "impossible", evidence: {}};
    expect(() => validateResultSemantics(contradictory)).toThrow("completed-step semantics");
    const stale = result("typescript");
    stale["diagnostic_state_revision"] = 0;
    expect(() => validateResultSemantics(stale)).toThrow("stale revision");
    const inconsistent = result("typescript");
    inconsistent["median_step_seconds"] = 123;
    expect(() => validateResultSemantics(inconsistent)).toThrow("inconsistent derived field");
  });

  it("rejects an empty strict comparison", async () => {
    const directory = await mkdtemp(join(tmpdir(), "foilbench-ts-empty-"));
    directories.push(directory);
    await expect(compareResults(directory, true, ["python", "julia", "typescript"])).rejects.toThrow("no result artifacts");
  });

  it("checks the Cartesian matrix and required-language roster", async () => {
    const results = ["stable-fluids", "lbm-d2q9", "pic-flip"].map((solver) => ({
      benchmark_matrix_id: "test", language: "typescript", solver,
      resolution: [32, 16], repetition: 1, success: true,
    }));
    await expect(assertCompleteMatrices(results, ["typescript", "python"])).rejects.toThrow("incomplete python artifacts");
    const first = results[0]; if (first === undefined) throw new Error("missing test result"); first["success"] = false;
    await expect(assertCompleteMatrices(results, ["typescript"])).rejects.toThrow("failed=1");
  });
});
