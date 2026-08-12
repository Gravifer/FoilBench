import {readFile} from "node:fs/promises";
import {resolve} from "node:path";
import {describe, expect, it} from "vitest";
import {NumericalFailure, type CanonicalFlowState, type Scenario, type SolverId} from "../../src/core/contracts.js";
import {NacaFoil} from "../../src/core/geometry.js";
import {bounds2d, dimensions} from "../../src/core/grid.js";
import {controlAt, parseScenario} from "../../src/core/scenario.js";
import {createSolver} from "../../src/solvers/factory.js";

const solverIds: readonly SolverId[] = ["stable-fluids", "lbm-d2q9", "pic-flip"];

async function uniformScenario(): Promise<Scenario> {
  const schema = JSON.parse(await readFile(resolve("../../spec/schemas/scenario.schema.json"), "utf8")) as object;
  const document = JSON.parse(await readFile(resolve("../../scenarios/validation/uniform.json"), "utf8")) as unknown;
  const scenario = parseScenario(document, schema);
  return {...scenario, domain: {...scenario.domain, resolution: [24, 12]}};
}

async function airfoilScenario(): Promise<Scenario> {
  const schema = JSON.parse(await readFile(resolve("../../spec/schemas/scenario.schema.json"), "utf8")) as object;
  const document = JSON.parse(await readFile(resolve("../../scenarios/airfoil/default.json"), "utf8")) as unknown;
  const scenario = parseScenario(document, schema);
  return {...scenario, domain: {...scenario.domain, resolution: [64, 32]}};
}

describe("shared solver protocol", () => {
  for (const solverId of solverIds) it(`${solverId} is deterministic, finite, and advances requested time`, async () => {
    const scenario = await uniformScenario();
    const first = createSolver(solverId); const second = createSolver(solverId);
    first.initialize(scenario, 17); second.initialize(scenario, 17);
    first.setReynolds(250); second.setReynolds(250);
    for (let step = 1; step <= 2; step += 1) {
      const control = controlAt(scenario, step * 0.02);
      const report = first.advance(control, 0.02); second.advance(control, 0.02);
      expect(report.requestedDt).toBe(0.02); expect(report.advancedDt).toBe(0.02);
    }
    const firstState = first.exportState(); const secondState = second.exportState();
    expect(firstState.time).toBeCloseTo(0.04, 12);
    expect(firstState.velocity.every(Number.isFinite)).toBe(true);
    expect([...firstState.velocity]).toEqual([...secondState.velocity]);
    expect(first.reynolds).toBe(250);
    for (const value of Object.values(first.diagnostics().values)) expect(Number.isFinite(value)).toBe(true);
  });

  for (const solverId of solverIds) it(`${solverId} rejects thin-3D through its capability boundary`, async () => {
    const scenario = await uniformScenario();
    const unsupported: Scenario = {
      ...scenario,
      domain: {...scenario.domain, dimension: 3, bounds: [...scenario.domain.bounds, [0, 0.25]], resolution: [...scenario.domain.resolution, 4], periodicAxes: ["z"]},
      freestream: [1, 0, 0],
      foil: {...scenario.foil, pivot: [0, 0, 0]},
    };
    expect(() => createSolver(solverId).initialize(unsupported, 0)).toThrow(/2D/);
  });

  for (const solverId of solverIds) it(`${solverId} restarts directly at an authoritative time, pose, and Reynolds number`, async () => {
    const scenario = await uniformScenario(); const solver = createSolver(solverId);
    solver.restart(scenario, 19, {time: 2.5, angleDegrees: 23, reynolds: 750});
    const state = solver.exportState();
    expect(state.time).toBe(2.5);
    expect(state.angleDegrees).toBe(23);
    expect(state.angularVelocityDegrees).toBe(0);
    expect(solver.reynolds).toBe(750);
    expect(state.velocity.every(Number.isFinite)).toBe(true);
  });

  for (const solverId of solverIds) it(`${solverId} rejects incompatible and malformed canonical state`, async () => {
    const scenario = await uniformScenario(); const solver = createSolver(solverId); solver.initialize(scenario, 0); const state = solver.exportState(); const control = controlAt(scenario, state.time);
    expect(solver.importState({...state, bounds: [[-2, 2], state.bounds[1] ?? [-1, 1]]}, control).reason).toBe("incompatible_domain");
    expect(solver.importState({...state, periodicAxes: []}, control).reason).toBe("incompatible_domain");
    expect(solver.importState({...state, precision: state.precision === "float32" ? "float64" : "float32"}, control).reason).toBe("incompatible_domain");
    expect(solver.importState({...state, velocity: state.velocity.slice(2)}, control).reason).toBe("incompatible_domain");
    expect(solver.importState({...state, time: Number.NaN}, control).reason).toBe("nonfinite_state");
    expect(solver.importState({...state, angleDegrees: state.angleDegrees + 1}, control).reason).toBe("time_contract_failure");
    expect(solver.importState({...state, angularVelocityDegrees: state.angularVelocityDegrees + 1}, control).reason).toBe("time_contract_failure");
    if (state.density !== null) expect(solver.importState({...state, density: state.density.slice(1)}, control).reason).toBe("incompatible_domain");
    expect(solver.importState({...state, schemaVersion: 2} as unknown as CanonicalFlowState, control).reason).toBe("incompatible_domain");
    const wrongVelocityType = state.velocity instanceof Float32Array
      ? new Float64Array(state.velocity)
      : new Float32Array(state.velocity);
    expect(solver.importState({...state, velocity: wrongVelocityType}, control).reason).toBe("incompatible_domain");
    if (state.density !== null) {
      const wrongDensityType = state.density instanceof Float32Array
        ? new Float64Array(state.density)
        : new Float32Array(state.density);
      expect(solver.importState({...state, density: wrongDensityType}, control).reason).toBe("incompatible_domain");
    }
  });

  for (const solverId of solverIds) it(`${solverId} rejects non-finite pose controls without mutation`, async () => {
    const scenario = await uniformScenario(); const solver = createSolver(solverId); solver.initialize(scenario, 0);
    const before = solver.exportState(); const revision = solver.stateRevision;
    for (const control of [
      {time: scenario.outputDt, angleDegrees: Number.NaN, angularVelocityDegrees: 0},
      {time: scenario.outputDt, angleDegrees: 0, angularVelocityDegrees: Number.POSITIVE_INFINITY},
    ]) {
      let failure: unknown;
      try { solver.advance(control, scenario.outputDt); } catch (error) { failure = error; }
      expect(failure).toBeInstanceOf(NumericalFailure);
      expect((failure as NumericalFailure).reason).toBe("time_contract_failure");
      expect((failure as NumericalFailure).stage).toBe("time-mapping");
      expect(solver.stateRevision).toBe(revision);
      expect([...solver.exportState().velocity]).toEqual([...before.velocity]);
    }
  });

  for (const solverId of solverIds) it(`${solverId} exports zero velocity at authoritative solid centers`, async () => {
    const scenario = await airfoilScenario(); const solver = createSolver(solverId); solver.initialize(scenario, 0);
    const angleDegrees = scenario.controls[0]?.angleDegrees ?? 0;
    const control = {time: scenario.outputDt, angleDegrees, angularVelocityDegrees: 120};
    solver.advance(control, scenario.outputDt); const state = solver.exportState(); const foil = new NacaFoil(scenario.foil);
    const {nx, ny, dx, dy} = dimensions(scenario.domain); const {x: bx, y: by} = bounds2d(scenario.domain); let solidCells = 0;
    for (let y = 0; y < ny; y += 1) for (let x = 0; x < nx; x += 1) if (foil.signedDistance(bx[0] + (x + 0.5) * dx, by[0] + (y + 0.5) * dy, state.angleDegrees) <= 0) {
      const cell = y * nx + x; solidCells += 1; expect(state.velocity[2 * cell]).toBe(0); expect(state.velocity[2 * cell + 1]).toBe(0);
    }
    expect(solidCells).toBeGreaterThan(0);
  });

  it("LBM canonical reconstruction is independent of output cadence", async () => {
    const base = await uniformScenario();
    const short = {...base, outputDt: 0.01}; const long = {...base, outputDt: 1};
    const source = createSolver("lbm-d2q9"); source.initialize(short, 0);
    const exported = source.exportState(); const velocity = new Float64Array(exported.velocity.length);
    for (let index = 0; index < velocity.length; index += 2) velocity[index] = 10;
    const state = {...exported, velocity}; const control = {time: state.time, angleDegrees: state.angleDegrees, angularVelocityDegrees: 0};
    const destinations = [short, long].map((scenario) => {
      const solver = createSolver("lbm-d2q9"); solver.initialize(scenario, 0);
      expect(solver.importState(state, control).status).toBe("accepted"); return solver;
    });
    const [first, second] = destinations;
    if (first === undefined || second === undefined) throw new Error("missing cadence destination");
    expect([...first.exportState().velocity]).toEqual([...second.exportState().velocity]);
    expect(first.diagnostics().values["effective_reynolds"]).toBeCloseTo(second.diagnostics().values["effective_reynolds"] ?? Number.NaN, 12);
  });
});
