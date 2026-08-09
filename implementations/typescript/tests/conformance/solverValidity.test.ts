import {readFile} from "node:fs/promises";
import {resolve} from "node:path";
import {describe, expect, it} from "vitest";
import type {Scenario, SolverId} from "../../src/core/contracts.js";
import {NumericalFailure} from "../../src/core/contracts.js";
import {parseScenario} from "../../src/core/scenario.js";
import {createSolver} from "../../src/solvers/factory.js";

const solverIds: readonly SolverId[] = ["stable-fluids", "lbm-d2q9", "pic-flip"];

async function loadScenario(path: string): Promise<Scenario> {
  const schema = JSON.parse(await readFile(resolve("../../spec/scenario.schema.json"), "utf8")) as object;
  const document = JSON.parse(await readFile(resolve(path), "utf8")) as unknown;
  const scenario = parseScenario(document, schema);
  return {...scenario, domain: {...scenario.domain, resolution: [32, 16]}};
}

function capturedFailure(operation: () => void): NumericalFailure {
  try { operation(); } catch (error) { if (error instanceof NumericalFailure) return error; throw error; }
  throw new Error("operation unexpectedly succeeded");
}

describe("revision-2 solver validity evidence", () => {
  for (const solverId of solverIds) it(`${solverId} revisions identify committed state and ignore rejection`, async () => {
    const scenario = await loadScenario("../../scenarios/validation/uniform.json"); const solver = createSolver(solverId); solver.initialize(scenario, 0);
    expect(solver.stateRevision).toBe(0);
    const reynolds = solver.setReynolds(350); expect(reynolds.requested).toBe(350); expect(solver.stateRevision).toBe(1);
    const report = solver.advance({time: 0.02, angleDegrees: 0, angularVelocityDegrees: 0}, 0.02);
    expect(report.stateRevision).toBe(2); expect(solver.stateRevision).toBe(2); expect(solver.diagnostics().stateRevision).toBe(2);
    const state = solver.exportState(); const rejected = solver.importState({...state, time: Number.NaN}, {time: state.time, angleDegrees: 0, angularVelocityDegrees: 0});
    expect(rejected.status).toBe("rejected"); expect(rejected.stage).toBe("canonical-import"); expect(solver.stateRevision).toBe(2);
  });

  for (const solverId of solverIds) it(`${solverId} rejects a mismatched completion time atomically`, async () => {
    const scenario = await loadScenario("../../scenarios/validation/uniform.json"); const solver = createSolver(solverId); solver.initialize(scenario, 0); const before = solver.exportState(); const revision = solver.stateRevision;
    const failure = capturedFailure(() => solver.advance({time: 1, angleDegrees: 0, angularVelocityDegrees: 0}, 0.02));
    expect(failure.reason).toBe("time_contract_failure"); expect(failure.stage).toBe("time-mapping"); expect(failure.evidence["expected_time"]).toBe(0.02);
    expect(solver.stateRevision).toBe(revision); expect([...solver.exportState().velocity]).toEqual([...before.velocity]); expect(solver.exportState().time).toBe(before.time);
  });

  for (const solverId of solverIds) it(`${solverId} bounds extreme wall motion without entering a long loop`, async () => {
    const scenario = await loadScenario("../../scenarios/validation/uniform.json"); const solver = createSolver(solverId); solver.initialize(scenario, 0); const before = solver.exportState(); const revision = solver.stateRevision;
    const failure = capturedFailure(() => solver.advance({time: 0.02, angleDegrees: 0, angularVelocityDegrees: 1e9}, 0.02));
    expect(["stability_limit", "excessive_velocity"]).toContain(failure.reason); expect(Number(failure.evidence["required_substeps"])).toBeGreaterThan(512);
    expect(solver.stateRevision).toBe(revision); expect([...solver.exportState().velocity]).toEqual([...before.velocity]); expect(solver.exportState().time).toBe(before.time);
  });

  it("reports family-specific accepted-step evidence", async () => {
    const scenario = await loadScenario("../../scenarios/validation/uniform.json");
    const stable = createSolver("stable-fluids"); stable.initialize(scenario, 0); const stableReport = stable.advance({time: 0.02, angleDegrees: 0, angularVelocityDegrees: 0}, 0.02);
    expect(stableReport.evidence["pressure_converged"]).toBe(true); expect(Number(stableReport.evidence["pressure_relative_residual"])).toBeGreaterThanOrEqual(0);
    const lbm = createSolver("lbm-d2q9"); lbm.initialize(scenario, 0); const lbmReport = lbm.advance({time: 0.02, angleDegrees: 0, angularVelocityDegrees: 0}, 0.02);
    expect(Number(lbmReport.evidence["maximum_lattice_mach"])).toBeLessThanOrEqual(0.08 * (1 + 1e-6)); expect(Number(lbmReport.evidence["trt_magic"])).toBeCloseTo(3 / 16, 10);
    const pic = createSolver("pic-flip"); pic.initialize(scenario, 0); const picReport = pic.advance({time: 0.02, angleDegrees: 0, angularVelocityDegrees: 0}, 0.02);
    expect(Number(picReport.evidence["maximum_particle_cfl"])).toBeLessThanOrEqual(0.75 * (1 + 1e-6)); expect(Number(picReport.evidence["particle_count"])).toBeGreaterThan(0); expect(Number(picReport.evidence["unsupported_face_fraction"])).toBeGreaterThanOrEqual(0); expect(picReport.evidence["pressure_converged"]).toBe(true);
  });

  it("classifies a finite pressure solve that exhausts its iteration budget", async () => {
    const scenario = await loadScenario("../../scenarios/airfoil/default.json"); const configured = {...scenario, domain: {...scenario.domain, resolution: [64, 32]}, freestream: [20, 0], solverOptions: {...scenario.solverOptions, pressureMaxIterations: 1, pressureTolerance: 1e-12}};
    const failure = capturedFailure(() => createSolver("stable-fluids").restart(configured, 0, {time: 0, angleDegrees: 30, reynolds: configured.reynolds}));
    expect(failure.reason).toBe("projection_failure"); expect(failure.stage).toBe("projection"); expect(failure.evidence["iterations"]).toBe(1);
  });
});
