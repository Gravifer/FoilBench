import {readFile} from "node:fs/promises";
import {resolve} from "node:path";
import {describe, expect, it} from "vitest";
import {controlAt, parseScenario} from "../../src/core/scenario.js";
import {PicFlipSolver} from "../../src/solvers/picFlip.js";

describe("blended PIC/FLIP contract", () => {
  it("advances deterministic solver particles and finite grid state", async () => { const schema = JSON.parse(await readFile(resolve("../../spec/scenario.schema.json"), "utf8")) as object; const raw = JSON.parse(await readFile(resolve("../../scenarios/validation/uniform.json"), "utf8")) as unknown; const scenario = parseScenario(raw, schema); const solver = new PicFlipSolver(); solver.initialize(scenario, scenario.seed); expect(solver.diagnostics().values["particle_count"]).toBe(4 * (scenario.domain.resolution[0] ?? 0) * (scenario.domain.resolution[1] ?? 0)); expect(solver.advance(controlAt(scenario, 0.01), 0.01).advancedDt).toBeCloseTo(0.01); expect(solver.exportState().velocity.every(Number.isFinite)).toBe(true); });
});
