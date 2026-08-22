import {readFile} from "node:fs/promises";
import {resolve} from "node:path";
import {describe, expect, it} from "vitest";
import {parseScenario} from "../../src/core/scenario.js";
import {normalizeViewerVorticity, ViewerModel} from "../../src/viewer/model.js";

describe("headless viewer model", () => {
  it("implements the shared vorticity-display fixture", async () => {
    const fixture = JSON.parse(await readFile(resolve("../../spec/conformance/vorticity-display.json"), "utf8")) as {contract_id: string; contract_revision: number; synthetic: {linear_count: number; linear_step: number; outlier: number; solid_outlier_expected_scale: number; fluid_outlier_expected_scale: number}};
    expect(fixture.contract_id).toBe("foilbench-phase2-v1"); expect(fixture.contract_revision).toBe(4);
    const {linear_count: count, linear_step: step, outlier, solid_outlier_expected_scale: maskedScale, fluid_outlier_expected_scale: fluidScale} = fixture.synthetic; const raw = new Float32Array(count + 1);
    for (let index = 0; index < count; index += 1) raw[index] = index * step; raw[count] = outlier;
    const solid = new Uint8Array(raw.length); solid[count] = 1; const masked = normalizeViewerVorticity(raw, solid);
    expect(masked[count]).toBe(0); expect(masked[count - 1]).toBeCloseTo(Math.tanh(1.99 / maskedScale), 6);
    const unmasked = normalizeViewerVorticity(raw, new Uint8Array(raw.length));
    expect(unmasked[count]).toBeCloseTo(Math.tanh(outlier / fluidScale), 6); expect(unmasked[count - 1]).toBeCloseTo(Math.tanh(1.99 / fluidScale), 6);
  });
  it("commits validation time and advances preserved tracers exactly once", async () => { const schema = JSON.parse(await readFile(resolve("../../spec/schemas/scenario.schema.json"), "utf8")) as object; const raw = JSON.parse(await readFile(resolve("../../scenarios/validation/uniform.json"), "utf8")) as unknown; const scenario = parseScenario(raw, schema); const model = new ViewerModel(scenario, "stable-fluids"); model.step(); const time = model.time; const before = model.tracers.positions.slice(); const epoch = model.solverEpoch; expect(model.switchSolver("lbm-d2q9")).toBe(true); expect(model.time).toBeCloseTo(time + scenario.outputDt, 12); expect(model.tracers.positions.length).toBe(before.length); expect([...model.tracers.positions]).not.toEqual([...before]); const snapshot = model.snapshot(); expect(snapshot.revision).toBe(1); expect(snapshot.solverEpoch).toBe(epoch + 1); expect(snapshot.solverStateRevision).toBe(model.solver.stateRevision); expect(snapshot.vorticityVisible).toBe(true); });
  it("clamps manual pose and toggles presentation state", async () => { const schema = JSON.parse(await readFile(resolve("../../spec/schemas/scenario.schema.json"), "utf8")) as object; const raw = JSON.parse(await readFile(resolve("../../scenarios/validation/uniform.json"), "utf8")) as unknown; const scenario = parseScenario(raw, schema); const model = new ViewerModel(scenario, "stable-fluids"); model.setAngle(80, 100); expect(model.control(0).angleDegrees).toBe(30); model.vorticityVisible = false; expect(model.snapshot().vorticity.every((value) => value === 0)).toBe(true); });
  it("rejects non-finite drag samples without mutating viewer state", async () => { const schema = JSON.parse(await readFile(resolve("../../spec/schemas/scenario.schema.json"), "utf8")) as object; const raw = JSON.parse(await readFile(resolve("../../scenarios/validation/uniform.json"), "utf8")) as unknown; const scenario = parseScenario(raw, schema); const model = new ViewerModel(scenario, "stable-fluids"); const before = model.control(0); expect(() => model.setAngle(Number.NaN, 100)).toThrow("pose angle"); expect(() => model.setAngle(5, Number.POSITIVE_INFINITY)).toThrow("pose timestamp"); expect(model.control(0)).toEqual(before); });

  it("publishes robustly normalized vorticity instead of boundary-dominated raw curl", async () => { const schema = JSON.parse(await readFile(resolve("../../spec/schemas/scenario.schema.json"), "utf8")) as object; const raw = JSON.parse(await readFile(resolve("../../scenarios/airfoil/default.json"), "utf8")) as unknown; const loaded = parseScenario(raw, schema); const scenario = {...loaded, domain: {...loaded.domain, resolution: [80, 48]}}; const model = new ViewerModel(scenario, "stable-fluids"); for (let step = 0; step < 4; step += 1) model.step(); const vorticity = model.snapshot().vorticity; expect(vorticity.length).toBe(80 * 48); expect(vorticity.every(Number.isFinite)).toBe(true); expect(Math.max(...vorticity.map(Math.abs))).toBeLessThanOrEqual(1); expect(vorticity.some((value) => Math.abs(value) > 0.2)).toBe(true); });
  it("treats non-monotonic drag events as a new zero-velocity sample", async () => {
    const schema = JSON.parse(await readFile(resolve("../../spec/schemas/scenario.schema.json"), "utf8")) as object;
    const raw = JSON.parse(await readFile(resolve("../../scenarios/validation/uniform.json"), "utf8")) as unknown;
    const scenario = parseScenario(raw, schema);
    const model = new ViewerModel(scenario, "stable-fluids");
    model.setAngle(0, 100); model.setAngle(20, 110);
    expect(Math.abs(model.control(0).angularVelocityDegrees)).toBeGreaterThan(0);
    model.setAngle(10, 105);
    expect(model.control(0).angularVelocityDegrees).toBe(0);
  });
  it("scenario reset resumes a paused owner without resetting presentation choices", async () => {
    const schema = JSON.parse(await readFile(resolve("../../spec/schemas/scenario.schema.json"), "utf8")) as object;
    const raw = JSON.parse(await readFile(resolve("../../scenarios/validation/uniform.json"), "utf8")) as unknown;
    const scenario = parseScenario(raw, schema);
    const model = new ViewerModel(scenario, "stable-fluids");
    model.paused = true; model.toggleDiagnostics(); model.reset();
    expect(model.paused).toBe(false);
    expect(model.snapshot().diagnosticMode).toBe("every-step");
  });
  it("starts a backend replacement at the authoritative interactive pose", async () => {
    const schema = JSON.parse(await readFile(resolve("../../spec/schemas/scenario.schema.json"), "utf8")) as object;
    const raw = JSON.parse(await readFile(resolve("../../scenarios/validation/uniform.json"), "utf8")) as unknown;
    const scenario = parseScenario(raw, schema);
    const model = new ViewerModel(scenario, "stable-fluids");
    model.restartInteractive(-12, 5000);
    const snapshot = model.snapshot();
    expect(snapshot.time).toBe(0);
    expect(snapshot.angleDegrees).toBe(-12);
    expect(snapshot.reynolds).toBe(5000);
    expect(snapshot.scheduleActive).toBe(false);
    expect(snapshot.status).toContain("backend restart");
    expect(snapshot.tracerPositions.every(Number.isFinite)).toBe(true);
  });
  it("keeps diagnostic cadence and per-solver tuning as presentation state", async () => {
    const schema = JSON.parse(await readFile(resolve("../../spec/schemas/scenario.schema.json"), "utf8")) as object;
    const raw = JSON.parse(await readFile(resolve("../../scenarios/validation/uniform.json"), "utf8")) as unknown;
    const scenario = parseScenario(raw, schema);
    const model = new ViewerModel(scenario, "stable-fluids");
    model.toggleDiagnostics(); model.adjustSolverTuning(1);
    expect(model.snapshot().diagnosticMode).toBe("every-step");
    expect(model.snapshot().solverTuning?.value).toBe("skew-rk2");
    expect(model.switchSolver("lbm-d2q9")).toBe(true);
    expect(model.switchSolver("stable-fluids")).toBe(true);
    expect(model.snapshot().solverTuning?.value).toBe("skew-rk2");
    model.reset();
    expect(model.snapshot().diagnosticMode).toBe("every-step");
    expect(model.snapshot().solverTuning?.value).toBe("maccormack");
  });
  it("fills reusable snapshot storage without exposing mutable solver arrays", async () => {
    const schema = JSON.parse(await readFile(resolve("../../spec/schemas/scenario.schema.json"), "utf8")) as object;
    const raw = JSON.parse(await readFile(resolve("../../scenarios/validation/uniform.json"), "utf8")) as unknown;
    const scenario = parseScenario(raw, schema);
    const model = new ViewerModel(scenario, "stable-fluids"); const storage = model.createSnapshotStorage();
    const first = model.snapshot(storage); model.step(); const second = model.snapshot(storage);
    expect(second.tracerPositions.buffer).toBe(first.tracerPositions.buffer);
    expect(second.pathSegments.buffer).toBe(first.pathSegments.buffer);
    expect(second.tracerPositions.buffer).not.toBe(model.tracers.positions.buffer);
  });
  it("keeps SPA trail metadata out of reference snapshots", async () => {
    const schema = JSON.parse(await readFile(resolve("../../spec/schemas/scenario.schema.json"), "utf8")) as object;
    const raw = JSON.parse(await readFile(resolve("../../scenarios/validation/uniform.json"), "utf8")) as unknown;
    const scenario = parseScenario(raw, schema);
    const model = new ViewerModel(scenario, "stable-fluids");
    const reference = model.snapshot(model.createSnapshotStorage());
    const spa = model.spaSnapshot(model.createSpaSnapshotStorage());
    expect("pathAges" in reference).toBe(false);
    expect(spa.pathAges.length).toBe(spa.pathSegments.length / 4);
  });
  it("uses quarter-decade Reynolds controls and measures owner cycles", async () => {
    const schema = JSON.parse(await readFile(resolve("../../spec/schemas/scenario.schema.json"), "utf8")) as object;
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
