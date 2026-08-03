import {readFile} from "node:fs/promises";
import {resolve} from "node:path";
import {describe, expect, it} from "vitest";
import type {SolverId} from "../../src/core/contracts.js";
import {parseScenario, validateDocument} from "../../src/core/scenario.js";
import {runDragCalibration} from "../../src/experiments/dragCalibration.js";

describe("matched drag calibration", () => {
  it("emits finite schema-valid records for every solver", async () => {
    const root = resolve("../..");
    const scenario = parseScenario(
      JSON.parse(await readFile(resolve(root, "scenarios/airfoil/chaotic-experimental.json"), "utf8")) as unknown,
      JSON.parse(await readFile(resolve(root, "spec/scenario.schema.json"), "utf8")) as object,
    );
    const schema = JSON.parse(await readFile(resolve(root, "spec/drag-calibration-result.schema.json"), "utf8")) as object;
    const candidate = {id: "conservative", tip_speed_cap: 4, smoothing_window_seconds: 0.08};
    const trace = {id: "gentle", samples: [[0, 0], [0.1, 4], [0.2, 8]] as const};
    const solvers: readonly SolverId[] = ["stable-fluids", "lbm-d2q9", "pic-flip"];
    const runs = solvers.map((solverId) => runDragCalibration(scenario, [24, 16], solverId, candidate, trace, 2));
    const result = {
      schema_version: 1,
      contract_id: "foilbench-phase2-v1-drag-calibration",
      language: "typescript",
      scenario: "scenarios/airfoil/chaotic-experimental.json",
      resolution: [24, 16],
      runs,
    };
    validateDocument(result, schema);
    expect(runs.every((run) => run.successful_steps === run.requested_steps)).toBe(true);
    expect(runs.every((run) => run.max_solver_tip_speed_ratio <= candidate.tip_speed_cap)).toBe(true);
    expect(runs.every((run) => Number.isFinite(run.maximum_flow_speed))).toBe(true);
  });
});
