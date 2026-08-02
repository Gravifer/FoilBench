import {readFile} from "node:fs/promises";
import {resolve} from "node:path";
import {describe, expect, it} from "vitest";
import {controlAt, parseScenario} from "../../src/core/scenario.js";
import {StableFluidsSolver} from "../../src/solvers/stableFluids.js";

async function scenario() { const schema = JSON.parse(await readFile(resolve("../../spec/scenario.schema.json"), "utf8")) as object; const document = JSON.parse(await readFile(resolve("../../scenarios/validation/uniform.json"), "utf8")) as unknown; return parseScenario(document, schema); }

describe("Stable Fluids contract", () => {
  it("advances requested time and exports finite canonical state", async () => { const selected = await scenario(); const solver = new StableFluidsSolver(); solver.initialize(selected, selected.seed); const report = solver.advance(controlAt(selected, 0.01), 0.01); expect(report.advancedDt).toBeCloseTo(0.01); expect(solver.exportState().velocity.every(Number.isFinite)).toBe(true); expect(solver.diagnostics().values["energy"]).toBeGreaterThan(0); });
  it("is deterministic", async () => { const selected = await scenario(); const first = new StableFluidsSolver(); const second = new StableFluidsSolver(); first.initialize(selected, 0); second.initialize(selected, 0); first.advance(controlAt(selected, 0.01), 0.01); second.advance(controlAt(selected, 0.01), 0.01); expect(Array.from(first.exportState().velocity)).toEqual(Array.from(second.exportState().velocity)); });
});
