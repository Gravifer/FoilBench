import {readFile} from "node:fs/promises";
import {resolve} from "node:path";
import {describe, expect, it} from "vitest";
import {parseScenario} from "../../src/core/scenario.js";
import {ViewerModel} from "../../src/viewer/model.js";

describe("headless viewer model", () => {
  it("commits validation time and preserves tracers through accepted switching", async () => { const schema = JSON.parse(await readFile(resolve("../../spec/scenario.schema.json"), "utf8")) as object; const raw = JSON.parse(await readFile(resolve("../../scenarios/validation/uniform.json"), "utf8")) as unknown; const scenario = parseScenario(raw, schema); const model = new ViewerModel(scenario, "stable-fluids"); model.step(); const time = model.time; const tracerCount = model.tracers.positions.length; expect(model.switchSolver("lbm-d2q9")).toBe(true); expect(model.time).toBeCloseTo(time + scenario.outputDt, 12); expect(model.tracers.positions.length).toBe(tracerCount); const snapshot = model.snapshot(); expect(snapshot.revision).toBe(1); expect(snapshot.vorticityVisible).toBe(true); });
  it("clamps manual pose and toggles presentation state", async () => { const schema = JSON.parse(await readFile(resolve("../../spec/scenario.schema.json"), "utf8")) as object; const raw = JSON.parse(await readFile(resolve("../../scenarios/validation/uniform.json"), "utf8")) as unknown; const scenario = parseScenario(raw, schema); const model = new ViewerModel(scenario, "stable-fluids"); model.setAngle(80, 100); expect(model.control(0).angleDegrees).toBe(30); model.vorticityVisible = false; expect(model.snapshot().vorticity.every((value) => value === 0)).toBe(true); });
  it("uses quarter-decade Reynolds controls and measures owner cycles", async () => {
    const schema = JSON.parse(await readFile(resolve("../../spec/scenario.schema.json"), "utf8")) as object;
    const raw = JSON.parse(await readFile(resolve("../../scenarios/validation/uniform.json"), "utf8")) as unknown;
    const scenario = parseScenario(raw, schema);
    const model = new ViewerModel(scenario, "stable-fluids");
    model.setReynolds(scenario.reynolds * 10);
    expect(model.playbackRate).toBeCloseTo(1.5, 12);
    expect(model.step()).toBeCloseTo(1.5 * scenario.outputDt, 12);
    model.recordOwnerCycle(1.5 * scenario.outputDt, 0.25);
    const snapshot = model.snapshot();
    expect(snapshot.stepRate).toBeCloseTo(4, 12);
    expect(snapshot.simulatedPerWall).toBeCloseTo(6 * scenario.outputDt, 12);
    model.setReynolds(1);
    expect(model.solver.reynolds).toBe(50);
    model.setReynolds(1_000_000);
    expect(model.solver.reynolds).toBe(100_000);
  });
});
