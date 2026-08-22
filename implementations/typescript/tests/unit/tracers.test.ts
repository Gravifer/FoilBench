import {readFile} from "node:fs/promises";
import {resolve} from "node:path";
import {describe, expect, it} from "vitest";
import type {
  CanonicalFlowState,
  ControlState,
  Diagnostics,
  FlowSolver,
  FloatArray,
  ImportOutcome,
  RestartState,
  ReynoldsOutcome,
  Scenario,
  SolverInfo,
  StepReport,
} from "../../src/core/contracts.js";
import {NacaFoil} from "../../src/core/geometry.js";
import {TracerSystem, defaultTracerCount} from "../../src/viewer/tracers.js";

const scenario: Scenario = {
  schemaVersion: 1,
  id: "tracer-unit",
  domain: {dimension: 2, bounds: [[-4, 4], [-2, 2]], resolution: [32, 16], periodicAxes: []},
  reynolds: 1000,
  freestream: [1, 0],
  foil: {naca: "0012", chord: 1, pivot: [-1, 0]},
  controls: [{time: 0, angleDegrees: 0}],
  duration: 1,
  outputDt: 0.01,
  precision: "float32",
  seed: 0,
  solverOptions: {},
};

type Velocity = (x: number, y: number) => readonly [number, number];

class SampleSolver implements FlowSolver {
  public readonly info: SolverInfo = {id: "stable-fluids", displayName: "sample", dimensions: [2], supportsMovingBoundary: true, supportedPrecisions: ["float32", "float64"], acceleration: "test"};
  public readonly reynolds = 1000;
  public readonly stateRevision = 0;

  public constructor(private readonly velocity: Velocity, private readonly angleDegrees = 0) {}
  public initialize(scenarioValue: Scenario, seed: number): void { void scenarioValue; void seed; }
  public restart(scenarioValue: Scenario, seed: number, start: RestartState): void { void scenarioValue; void seed; void start; }
  public setReynolds(reynolds: number): ReynoldsOutcome { return {requested: reynolds, effective: reynolds, warnings: []}; }
  public advance(_control: ControlState, targetDt: number): StepReport { return {requestedDt: targetDt, advancedDt: targetDt, substeps: 1, maxSpeed: 0, stateRevision: 0, evidence: {}, warnings: []}; }
  public sampleVelocity(points: FloatArray): FloatArray {
    const sampled = points instanceof Float32Array ? new Float32Array(points.length) : new Float64Array(points.length);
    for (let index = 0; index < points.length; index += 2) {
      const [vx, vy] = this.velocity(points[index] ?? 0, points[index + 1] ?? 0);
      sampled[index] = vx; sampled[index + 1] = vy;
    }
    return sampled;
  }
  public exportState(): CanonicalFlowState {
    return {schemaVersion: 1, dimension: 2, bounds: scenario.domain.bounds, resolution: scenario.domain.resolution, periodicAxes: [], time: 0, precision: "float32", angleDegrees: this.angleDegrees, angularVelocityDegrees: 0, sourceLanguage: "test", sourceSolver: "sample", velocity: new Float32Array(32 * 16 * 2), density: null};
  }
  public importState(state: CanonicalFlowState, control: ControlState): ImportOutcome { void state; void control; return {status: "accepted", reason: "none", stage: null, evidence: {}, discardedState: [], warnings: []}; }
  public diagnostics(): Diagnostics { return {stateRevision: 0, values: {}, warnings: []}; }
}

describe("visible tracer contract", () => {
  it("uses the shared frozen-field explicit-midpoint fixture rather than Euler", async () => {
    const fixture = JSON.parse(await readFile(resolve("../../spec/conformance/tracer-lifecycle.json"), "utf8")) as {contract_id: string; contract_revision: number; integrator: {initial_position: [number, number]; target_dt: number; expected_position: [number, number]; absolute_tolerance: number}};
    expect(fixture.contract_id).toBe("foilbench-phase2-v1"); expect(fixture.contract_revision).toBe(4);
    const tracers = new TracerSystem(scenario, 1, 3);
    tracers.positions[0] = fixture.integrator.initial_position[0]; tracers.positions[1] = fixture.integrator.initial_position[1];
    tracers.ages[0] = 0; tracers.lifetimes[0] = 10;
    tracers.advance(new SampleSolver((x, y) => [-y, x]), fixture.integrator.target_dt);
    expect(tracers.positions[0]).toBeCloseTo(fixture.integrator.expected_position[0], Math.ceil(-Math.log10(fixture.integrator.absolute_tolerance)));
    expect(tracers.positions[1]).toBeCloseTo(fixture.integrator.expected_position[1], Math.ceil(-Math.log10(fixture.integrator.absolute_tolerance)));
  });

  it("is deterministic within the TypeScript implementation", () => {
    const first = new TracerSystem(scenario, 32, 3);
    const second = new TracerSystem(scenario, 32, 3);
    expect([...first.positions]).toEqual([...second.positions]);
    expect([...first.ages]).toEqual([...second.ages]);
    expect([...first.lifetimes]).toEqual([...second.lifetimes]);
  });

  it("uses the authoritative foil pose for full reseeding", () => {
    const tracers = new TracerSystem(scenario, 128, 3);
    tracers.reseed(30, "forced_recovery");
    const foil = new NacaFoil(scenario.foil);
    for (let index = 0; index < tracers.count; index += 1) {
      expect(foil.signedDistance(tracers.positions[2 * index] ?? 0, tracers.positions[2 * index + 1] ?? 0, 30)).toBeGreaterThan(0);
    }
    expect(tracers.recycleCounters.forced_recovery).toBe(128);
  });

  it("recycles an open-boundary exit at the inlet in either mode", () => {
    const tracers = new TracerSystem(scenario, 1, 3);
    tracers.positions[0] = 3.99; tracers.positions[1] = 1.5;
    tracers.advance(new SampleSolver(() => [2, 0]), 0.1);
    expect(tracers.positions[0]).toBeGreaterThanOrEqual(-4);
    expect(tracers.positions[0]).toBeLessThan(-3.9);
    expect(tracers.recycleCounters.boundary_exit).toBe(1);
  });

  it("expires display tracers across the full domain but not material tracers", () => {
    const display = new TracerSystem(scenario, 1, 3);
    display.positions[0] = 2; display.positions[1] = 1;
    display.ages[0] = display.lifetimes[0] ?? 0;
    display.advance(new SampleSolver(() => [0, 0]), 0.01);
    expect(display.recycleCounters.lifetime_expiry).toBe(1);

    const material = new TracerSystem(scenario, 1, 3);
    material.setMode("material");
    material.positions[0] = 2; material.positions[1] = 1;
    material.ages[0] = material.lifetimes[0] ?? 0;
    material.advance(new SampleSolver(() => [0, 0]), 0.01);
    expect(material.recycleCounters.lifetime_expiry).toBe(0);
    expect(material.positions[0]).toBe(2);
  });

  it("publishes compact age metadata aligned with each continuous path segment", () => {
    const tracers = new TracerSystem(scenario, 1, 3);
    tracers.positions[0] = 2; tracers.positions[1] = 1;
    tracers.ages[0] = 0; tracers.lifetimes[0] = 10;
    tracers.advance(new SampleSolver(() => [1, 0]), 0.01);
    const path = tracers.segmentsWithAges();
    expect(path.segments.length).toBe(4 * path.ages.length);
    expect([...path.ages]).toEqual([0, 255]);
    expect(path.segments[6] ?? 0).toBeGreaterThan(path.segments[4] ?? 0);
  });

  it("derives the ordinary population from visible domain area", () => {
    expect(defaultTracerCount(scenario)).toBe(8192);
    expect(defaultTracerCount({...scenario, domain: {...scenario.domain, bounds: [[-1, 1], [-0.5, 0.5]]}})).toBe(2048);
  });
});
