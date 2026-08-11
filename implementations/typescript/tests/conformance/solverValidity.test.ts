import {readFile} from "node:fs/promises";
import {resolve} from "node:path";
import {describe, expect, it} from "vitest";
import type {Scenario, SolverId} from "../../src/core/contracts.js";
import {NumericalFailure} from "../../src/core/contracts.js";
import {parseScenario} from "../../src/core/scenario.js";
import {createSolver} from "../../src/solvers/factory.js";

const solverIds: readonly SolverId[] = ["stable-fluids", "lbm-d2q9", "pic-flip"];
interface ValidityFixture {
  readonly contract_id: string;
  readonly contract_revision: number;
  readonly scenario: string;
  readonly resolution: readonly [number, number];
  readonly target_dt: number;
  readonly accepted_evidence: Readonly<Record<SolverId, readonly string[]>>;
  readonly limits: {readonly lbm_maximum_mach: number; readonly lbm_trt_magic: number; readonly pic_maximum_particle_cfl: number};
}

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
    const fixture = JSON.parse(await readFile(resolve("../../spec/conformance/solver-validity.json"), "utf8")) as ValidityFixture;
    expect(fixture.contract_id).toBe("foilbench-phase2-v1"); expect(fixture.contract_revision).toBe(3);
    const scenario = await loadScenario(`../../${fixture.scenario}`);
    const reports = Object.fromEntries(solverIds.map((solverId) => { const solver = createSolver(solverId); solver.initialize(scenario, 0); return [solverId, solver.advance({time: fixture.target_dt, angleDegrees: 0, angularVelocityDegrees: 0}, fixture.target_dt)]; })) as Record<SolverId, ReturnType<ReturnType<typeof createSolver>["advance"]>>;
    for (const solverId of solverIds) for (const key of fixture.accepted_evidence[solverId]) expect(reports[solverId].evidence[key]).not.toBeUndefined();
    expect(reports["stable-fluids"].evidence["pressure_converged"]).toBe(true);
    expect(Number(reports["lbm-d2q9"].evidence["maximum_lattice_mach"])).toBeLessThanOrEqual(fixture.limits.lbm_maximum_mach * (1 + 1e-6)); expect(Number(reports["lbm-d2q9"].evidence["trt_magic"])).toBeCloseTo(fixture.limits.lbm_trt_magic, 10);
    expect(Number(reports["pic-flip"].evidence["maximum_particle_cfl"])).toBeLessThanOrEqual(fixture.limits.pic_maximum_particle_cfl * (1 + 1e-6)); expect(Number(reports["pic-flip"].evidence["particle_count"])).toBeGreaterThan(0); expect(Number(reports["pic-flip"].evidence["unsupported_face_fraction"])).toBeGreaterThanOrEqual(0); expect(reports["pic-flip"].evidence["pressure_converged"]).toBe(true);
  });

  it("classifies a finite pressure solve that exhausts its iteration budget", async () => {
    const scenario = await loadScenario("../../scenarios/airfoil/default.json"); const configured = {...scenario, domain: {...scenario.domain, resolution: [64, 32]}, freestream: [20, 0], solverOptions: {...scenario.solverOptions, pressureMaxIterations: 1, pressureTolerance: 1e-12}};
    const failure = capturedFailure(() => createSolver("stable-fluids").restart(configured, 0, {time: 0, angleDegrees: 30, reynolds: configured.reynolds}));
    expect(failure.reason).toBe("projection_failure"); expect(failure.stage).toBe("projection"); expect(failure.evidence["iterations"]).toBe(1);
  });
});
