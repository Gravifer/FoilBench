import {readFile} from "node:fs/promises";
import {resolve} from "node:path";
import {describe, expect, it} from "vitest";
import type {CanonicalFlowState, ControlState, Diagnostics, FlowSolver, FloatArray, ImportOutcome, Scenario, SolverInfo, StepReport} from "../../src/core/contracts.js";
import {NumericalFailure} from "../../src/core/contracts.js";
import {parseScenario} from "../../src/core/scenario.js";
import {ViewerModel} from "../../src/viewer/model.js";

class FailingSolver implements FlowSolver {
  public constructor(private readonly inner: FlowSolver, private readonly error: Error) {}
  public get info(): SolverInfo { return this.inner.info; }
  public get reynolds(): number { return this.inner.reynolds; }
  public initialize(scenario: Scenario, seed: number): void { this.inner.initialize(scenario, seed); }
  public setReynolds(reynolds: number): void { this.inner.setReynolds(reynolds); }
  public advance(control: ControlState, targetDt: number): StepReport { void control; void targetDt; throw this.error; }
  public sampleVelocity(points: FloatArray): FloatArray { return this.inner.sampleVelocity(points); }
  public exportState(): CanonicalFlowState { return this.inner.exportState(); }
  public importState(state: CanonicalFlowState, control: ControlState): ImportOutcome { return this.inner.importState(state, control); }
  public diagnostics(): Diagnostics { return this.inner.diagnostics(); }
}

async function scenario(): Promise<Scenario> {
  const schema = JSON.parse(await readFile(resolve("../../spec/scenario.schema.json"), "utf8")) as object;
  const document = JSON.parse(await readFile(resolve("../../scenarios/validation/uniform.json"), "utf8")) as unknown;
  const parsed = parseScenario(document, schema);
  return {...parsed, domain: {...parsed.domain, resolution: [24, 12]}};
}

const numericalFailure = (): NumericalFailure => new NumericalFailure("nonfinite_state", "injected numerical failure");

describe("interactive recovery semantics", () => {
  it("recovers numerical failure at the visible pose without advancing time", async () => {
    const model = new ViewerModel(await scenario(), "stable-fluids");
    model.step(); const completedTime = model.time;
    model.setAngle(20, 100); const before = model.tracers.positions.slice();
    model.solver = new FailingSolver(model.solver, numericalFailure());
    model.step(); const recovered = model.snapshot();
    expect(model.time).toBe(completedTime);
    expect(recovered.angleDegrees).toBe(20);
    expect(recovered.recoveryEpoch).toBe(1);
    expect(recovered.scheduleActive).toBe(false);
    expect(recovered.stepRate).toBeNull();
    expect(Object.keys(recovered.diagnostics)).toHaveLength(0);
    expect([...model.tracers.positions]).not.toEqual([...before]);
    model.releaseAngle(); model.step();
    expect(model.time).toBeCloseTo(completedTime + model.scenario.outputDt, 12);
  });

  it("does not discard flow state for an unexpected programming error", async () => {
    const model = new ViewerModel(await scenario(), "stable-fluids"); const failing = new FailingSolver(model.solver, new TypeError("injected bug")); model.solver = failing;
    model.step();
    expect(model.paused).toBe(true);
    expect(model.solver).toBe(failing);
    expect(model.snapshot().recoveryEpoch).toBe(0);
  });

  it("enters pose-only after consecutive rapid-motion failures and releases after mouse-up", async () => {
    const model = new ViewerModel(await scenario(), "stable-fluids");
    model.setAngle(0, 100); model.setAngle(20, 110);
    model.solver = new FailingSolver(model.solver, numericalFailure()); model.step();
    model.setAngle(22, 120); model.setAngle(25, 130); model.solver = new FailingSolver(model.solver, numericalFailure()); model.step();
    expect(model.snapshot().poseOnly).toBe(true);
    model.releaseAngle(); model.step();
    expect(model.snapshot().poseOnly).toBe(false);
  });

  it("resets an unstable online Reynolds selection before pausing at baseline", async () => {
    const model = new ViewerModel(await scenario(), "stable-fluids"); model.setReynolds(10_000);
    for (let failure = 0; failure < 3; failure += 1) { model.solver = new FailingSolver(model.solver, numericalFailure()); model.step(); }
    expect(model.solver.reynolds).toBe(model.scenario.reynolds);
    expect(model.paused).toBe(false);
    model.solver = new FailingSolver(model.solver, numericalFailure()); model.step();
    expect(model.paused).toBe(true);
  });
});
