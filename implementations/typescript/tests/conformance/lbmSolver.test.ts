import {readFile} from "node:fs/promises";
import {resolve} from "node:path";
import {describe, expect, it} from "vitest";
import {controlAt, parseScenario} from "../../src/core/scenario.js";
import {LbmSolver} from "../../src/solvers/lbm.js";

describe("D2Q9 TRT LBM contract", () => {
  it("advances finite populations and canonical fields", async () => { const schema = JSON.parse(await readFile(resolve("../../spec/scenario.schema.json"), "utf8")) as object; const raw = JSON.parse(await readFile(resolve("../../scenarios/validation/uniform.json"), "utf8")) as unknown; const scenario = parseScenario(raw, schema); const solver = new LbmSolver(); solver.initialize(scenario, 0); expect(solver.advance(controlAt(scenario, 0.01), 0.01).advancedDt).toBeCloseTo(0.01); expect(solver.exportState().velocity.every(Number.isFinite)).toBe(true); expect(solver.exportState().density?.every(Number.isFinite)).toBe(true); });
  it("classifies invalid density and excessive imported velocity", async () => { const schema = JSON.parse(await readFile(resolve("../../spec/scenario.schema.json"), "utf8")) as object; const raw = JSON.parse(await readFile(resolve("../../scenarios/validation/uniform.json"), "utf8")) as unknown; const scenario = parseScenario(raw, schema); const solver = new LbmSolver(); solver.initialize(scenario, 0); const control = controlAt(scenario, 0); const state = solver.exportState(); if (state.density === null) throw new Error("LBM density missing"); const invalidDensity = state.density.slice(); invalidDensity[0] = -1; expect(solver.importState({...state, density: invalidDensity}, control).reason).toBe("invalid_density"); const excessive = state.velocity.slice(); excessive[0] = 2; expect(solver.importState({...state, velocity: excessive}, control).reason).toBe("excessive_velocity"); });
});
