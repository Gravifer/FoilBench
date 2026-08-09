import {readFile} from "node:fs/promises";
import {resolve} from "node:path";
import {describe, expect, it} from "vitest";
import type {Scenario, SolverId} from "../../src/core/contracts.js";
import {controlAt, parseScenario} from "../../src/core/scenario.js";
import {createSolver} from "../../src/solvers/factory.js";

const solverIds: readonly SolverId[] = ["stable-fluids", "lbm-d2q9", "pic-flip"];

async function uniformScenario(): Promise<Scenario> {
  const schema = JSON.parse(await readFile(resolve("../../spec/scenario.schema.json"), "utf8")) as object;
  const document = JSON.parse(await readFile(resolve("../../scenarios/validation/uniform.json"), "utf8")) as unknown;
  const scenario = parseScenario(document, schema);
  return {...scenario, domain: {...scenario.domain, resolution: [24, 12]}};
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
    expect(solver.importState({...state, velocity: state.velocity.slice(2)}, control).reason).toBe("incompatible_domain");
    expect(solver.importState({...state, time: Number.NaN}, control).reason).toBe("nonfinite_state");
    if (state.density !== null) expect(solver.importState({...state, density: state.density.slice(1)}, control).reason).toBe("incompatible_domain");
  });
});
