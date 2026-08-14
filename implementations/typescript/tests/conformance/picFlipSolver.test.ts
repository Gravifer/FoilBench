import {readFile} from "node:fs/promises";
import {resolve} from "node:path";
import {describe, expect, it} from "vitest";
import {controlAt, parseScenario} from "../../src/core/scenario.js";
import {PicFlipSolver} from "../../src/solvers/picFlip.js";
import type {StableCheckpoint} from "../../src/solvers/stableFluids.js";

describe("blended PIC/FLIP contract", () => {
  it("advances deterministic solver particles and finite grid state", async () => { const schema = JSON.parse(await readFile(resolve("../../spec/schemas/scenario.schema.json"), "utf8")) as object; const raw = JSON.parse(await readFile(resolve("../../scenarios/validation/uniform.json"), "utf8")) as unknown; const scenario = parseScenario(raw, schema); const solver = new PicFlipSolver(); solver.initialize(scenario, scenario.seed); expect(solver.diagnostics().values["particle_count"]).toBe(4 * (scenario.domain.resolution[0] ?? 0) * (scenario.domain.resolution[1] ?? 0)); expect(solver.advance(controlAt(scenario, 0.01), 0.01).advancedDt).toBeCloseTo(0.01); expect(solver.exportState().velocity.every(Number.isFinite)).toBe(true); });
  it("uses the configured particle CFL and rejects invalid values", async () => { const schema = JSON.parse(await readFile(resolve("../../spec/schemas/scenario.schema.json"), "utf8")) as object; const raw = JSON.parse(await readFile(resolve("../../scenarios/validation/uniform.json"), "utf8")) as unknown; const loaded = parseScenario(raw, schema); const scenario = {...loaded, domain: {...loaded.domain, resolution: [24, 12]}, solverOptions: {...loaded.solverOptions, picCfl: 0.25}}; const solver = new PicFlipSolver(); solver.initialize(scenario, 0); expect(solver.advance(controlAt(scenario, 0.1), 0.1).substeps).toBeGreaterThan(1); const invalid = new PicFlipSolver(); expect(() => invalid.initialize({...scenario, solverOptions: {...scenario.solverOptions, picCfl: 1.1}}, 0)).toThrow(/pic_cfl/); });
  it("reports deterministic PIC-dominant settling after canonical import", async () => { const schema = JSON.parse(await readFile(resolve("../../spec/schemas/scenario.schema.json"), "utf8")) as object; const raw = JSON.parse(await readFile(resolve("../../scenarios/validation/uniform.json"), "utf8")) as unknown; const scenario = parseScenario(raw, schema); const source = new PicFlipSolver(); source.initialize(scenario, 0); const destination = new PicFlipSolver(); destination.initialize(scenario, 0); const outcome = destination.importState(source.exportState(), controlAt(scenario, 0)); expect(outcome.status).toBe("accepted"); expect(outcome.warnings).toContain("solver particles reseeded; first step is PIC-dominant"); expect(destination.advance(controlAt(scenario, 0.01), 0.01).advancedDt).toBe(0.01); });
  it("resolves collisions through a swept foil pose", async () => {
    const schema = JSON.parse(await readFile(resolve("../../spec/schemas/scenario.schema.json"), "utf8")) as object;
    const raw = JSON.parse(await readFile(resolve("../../scenarios/airfoil/default.json"), "utf8")) as unknown;
    const loaded = parseScenario(raw, schema);
    const scenario = {...loaded, domain: {...loaded.domain, resolution: [80, 40]}, outputDt: 1 / 60, solverOptions: {...loaded.solverOptions, macMaximumDivergenceLinf: 10}};
    const solver = new PicFlipSolver(); solver.initialize(scenario, 0);
    const report = solver.advance({time: scenario.outputDt, angleDegrees: 30, angularVelocityDegrees: 0}, scenario.outputDt);
    expect(report.substeps).toBeGreaterThan(1);
    expect(Number(report.evidence["swept_collisions_last_step"])).toBeGreaterThan(0);
    expect(solver.diagnostics().values["particles_inside_solid"]).toBe(0);
  });
  it("keeps periodic seam crossings as short unwrapped collision segments", async () => {
    const schema = JSON.parse(await readFile(resolve("../../spec/schemas/scenario.schema.json"), "utf8")) as object;
    const raw = JSON.parse(await readFile(resolve("../../scenarios/validation/uniform.json"), "utf8")) as unknown;
    const loaded = parseScenario(raw, schema);
    const scenario = {
      ...loaded,
      domain: {...loaded.domain, resolution: [40, 20]},
      foil: {...loaded.foil, chord: 0.2, pivot: [1, 0.5]},
    };
    const solver = new PicFlipSolver(); solver.initialize(scenario, 0);
    type BoundaryHarness = {
      readonly x: Float64Array;
      readonly y: Float64Array;
      readonly vx: Float64Array;
      readonly vy: Float64Array;
      sweptCollisionsLastStep: number;
      applyParticleBoundary(index: number, oldX: number, oldY: number, start: ReturnType<typeof controlAt>, control: ReturnType<typeof controlAt>, faces: StableCheckpoint): void;
    };
    const harness = solver as unknown as BoundaryHarness;
    harness.x[0] = 2.01; harness.y[0] = 0.5; harness.vx[0] = 1; harness.vy[0] = 0; harness.sweptCollisionsLastStep = 0;
    const faces: StableCheckpoint = {
      u: new Float64Array(20 * 41), v: new Float64Array(21 * 40), solid: new Uint8Array(20 * 40),
      time: 0, control: controlAt(scenario, 0), stateRevision: 0,
      projection: {criterion: "relative-residual-l2", tolerance: 0, iterations: 0, finalResidual: 0, relativeResidual: 0, divergenceLinf: 0, converged: true},
      viscosity: {criterion: "update-linf", tolerance: 0, iterations: 0, finalResidual: 0, converged: true},
    };
    harness.applyParticleBoundary(0, 1.99, 0.5, controlAt(scenario, 0), controlAt(scenario, 0), faces);
    expect(harness.sweptCollisionsLastStep).toBe(0);
    expect(harness.x[0]).toBeCloseTo(0.01);
    expect(harness.y[0]).toBeCloseTo(0.5);
  });
});
