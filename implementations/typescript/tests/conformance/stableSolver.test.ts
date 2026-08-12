import {readFile} from "node:fs/promises";
import {resolve} from "node:path";
import {describe, expect, it} from "vitest";
import {controlAt, parseScenario} from "../../src/core/scenario.js";
import {StableFluidsSolver} from "../../src/solvers/stableFluids.js";

async function scenario() { const schema = JSON.parse(await readFile(resolve("../../spec/schemas/scenario.schema.json"), "utf8")) as object; const document = JSON.parse(await readFile(resolve("../../scenarios/validation/uniform.json"), "utf8")) as unknown; return parseScenario(document, schema); }

describe("Stable Fluids contract", () => {
  it("advances requested time and exports finite canonical state", async () => { const selected = await scenario(); const solver = new StableFluidsSolver(); solver.initialize(selected, selected.seed); const report = solver.advance(controlAt(selected, 0.01), 0.01); expect(report.advancedDt).toBeCloseTo(0.01); expect(solver.exportState().velocity.every(Number.isFinite)).toBe(true); expect(solver.diagnostics().values["energy"]).toBeGreaterThan(0); });
  it("is deterministic", async () => { const selected = await scenario(); const first = new StableFluidsSolver(); const second = new StableFluidsSolver(); first.initialize(selected, 0); second.initialize(selected, 0); first.advance(controlAt(selected, 0.01), 0.01); second.advance(controlAt(selected, 0.01), 0.01); expect(Array.from(first.exportState().velocity)).toEqual(Array.from(second.exportState().velocity)); });
  for (const mode of ["semi-lagrangian", "maccormack", "skew-rk2"] as const) it(`executes finite ${mode} transport`, async () => { const selected = await scenario(); const configured = {...selected, solverOptions: {...selected.solverOptions, stableAdvection: mode}}; const solver = new StableFluidsSolver(); solver.initialize(configured, 0); expect(solver.transportMode).toBe(mode); solver.advance(controlAt(configured, 0.01), 0.01); expect(solver.exportState().velocity.every(Number.isFinite)).toBe(true); });
  it("treats duplicated periodic MAC endpoints as one logical face", async () => {
    const selected = await scenario(); const [nx, ny] = selected.domain.resolution as [number, number]; const solver = new StableFluidsSolver(); solver.initialize(selected, 0);
    const u = new Float64Array(ny * (nx + 1)); const v = new Float64Array((ny + 1) * nx);
    for (let y = 0; y < ny; y += 1) for (let x = 0; x <= nx; x += 1) u[y * (nx + 1) + x] = Math.sin(2 * Math.PI * x / nx);
    for (let y = 0; y <= ny; y += 1) for (let x = 0; x < nx; x += 1) v[y * nx + x] = 0.25 * Math.sin(2 * Math.PI * y / ny);
    const exposed = solver as unknown as {skewConvectionFaces(a: Float64Array, b: Float64Array): {u: Float64Array; v: Float64Array}}; const result = exposed.skewConvectionFaces(u, v);
    for (let y = 0; y < ny; y += 1) expect(result.u[y * (nx + 1) + nx] ?? 0).toBeCloseTo(result.u[y * (nx + 1)] ?? 0, 12);
    for (let x = 0; x < nx; x += 1) expect(result.v[ny * nx + x] ?? 0).toBeCloseTo(result.v[x] ?? 0, 12);
  });
});
