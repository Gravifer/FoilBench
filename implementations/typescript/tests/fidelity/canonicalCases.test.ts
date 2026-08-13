import {readFile} from "node:fs/promises";
import {resolve} from "node:path";
import {describe, expect, it} from "vitest";
import type {Scenario, SolverId} from "../../src/core/contracts.js";
import {controlAt, parseScenario} from "../../src/core/scenario.js";
import {createSolver} from "../../src/solvers/factory.js";

interface FidelityMetric {
  readonly comparison: "maximum" | "finite";
  readonly threshold: number | null;
}

interface FidelityCase {
  readonly id: string;
  readonly scenario: string;
  readonly resolution: readonly [number, number];
  readonly duration: number;
  readonly metrics: Readonly<Record<string, FidelityMetric>>;
}

interface FidelityFixture {
  readonly cases: FidelityCase[];
}

const solverIds: readonly SolverId[] = ["stable-fluids", "lbm-d2q9", "pic-flip"];

async function fixtureCase(id: string): Promise<FidelityCase> {
  const fixture = JSON.parse(
    await readFile(resolve("../../spec/conformance/fidelity-cases.json"), "utf8"),
  ) as FidelityFixture;
  const selected = fixture.cases.find((value) => value.id === id);
  if (selected === undefined) throw new RangeError(`unknown fidelity case ${id}`);
  return selected;
}

async function scenarioFor(id: string): Promise<{scenario: Scenario; selected: FidelityCase}> {
  const selected = await fixtureCase(id);
  const schema = JSON.parse(
    await readFile(resolve("../../spec/schemas/scenario.schema.json"), "utf8"),
  ) as object;
  const document = JSON.parse(await readFile(resolve(`../../${selected.scenario}`), "utf8")) as unknown;
  const loaded = parseScenario(document, schema);
  return {
    selected,
    scenario: {
      ...loaded,
      domain: {...loaded.domain, resolution: selected.resolution},
      duration: selected.duration,
    },
  };
}

function advance(solverId: SolverId, scenario: Scenario): ReturnType<typeof createSolver> {
  const solver = createSolver(solverId);
  solver.initialize(scenario, scenario.seed);
  const steps = Math.round(scenario.duration / scenario.outputDt);
  for (let step = 0; step < steps; step += 1) {
    solver.advance(controlAt(scenario, (step + 1) * scenario.outputDt), scenario.outputDt);
  }
  return solver;
}

function threshold(selected: FidelityCase, metric: string): number {
  const value = selected.metrics[metric]?.threshold;
  if (value === null || value === undefined) throw new RangeError(`metric ${metric} has no threshold`);
  return value;
}

describe("matched canonical fidelity scenarios", () => {
  for (const solverId of solverIds) it(`${solverId} preserves uniform flow`, async () => {
    const {scenario, selected} = await scenarioFor("uniform");
    const solver = createSolver(solverId);
    solver.initialize(scenario, scenario.seed);
    const before = solver.exportState();
    const afterSolver = advance(solverId, scenario);
    const after = afterSolver.exportState();
    let velocityError = 0; let vorticityError = 0; let densityError = 0;
    const [nx, ny] = selected.resolution;
    for (let index = 0; index < after.velocity.length; index += 1) velocityError += ((after.velocity[index] ?? 0) - (before.velocity[index] ?? 0)) ** 2;
    for (let y = 1; y + 1 < ny; y += 1) for (let x = 1; x + 1 < nx; x += 1) {
      const dvDx = ((after.velocity[2 * (y * nx + x + 1) + 1] ?? 0) - (after.velocity[2 * (y * nx + x - 1) + 1] ?? 0)) / 2;
      const duDy = ((after.velocity[2 * ((y + 1) * nx + x)] ?? 0) - (after.velocity[2 * ((y - 1) * nx + x)] ?? 0)) / 2;
      vorticityError += (dvDx - duDy) ** 2;
    }
    if (before.density !== null && after.density !== null) for (let index = 0; index < after.density.length; index += 1) densityError = Math.max(densityError, Math.abs((after.density[index] ?? 0) - (before.density[index] ?? 0)));
    expect(Math.sqrt(velocityError / after.velocity.length)).toBeLessThan(threshold(selected, "velocity_rms_drift"));
    expect(Math.sqrt(vorticityError / (nx * ny))).toBeLessThan(threshold(selected, "spurious_vorticity_rms"));
    expect(densityError).toBeLessThan(threshold(selected, "density_linf_drift"));
  });

  for (const solverId of solverIds) it(`${solverId} follows Taylor-Green decay`, async () => {
    const {scenario, selected} = await scenarioFor("taylor-green");
    const solver = createSolver(solverId); solver.initialize(scenario, scenario.seed);
    const before = solver.diagnostics().values["kinetic_energy"] ?? 0;
    const advanced = advance(solverId, scenario); const velocity = advanced.exportState().velocity;
    const [nx, ny] = selected.resolution; const boundsX = scenario.domain.bounds[0]; const boundsY = scenario.domain.bounds[1];
    if (boundsX === undefined || boundsY === undefined) throw new RangeError("2D bounds missing");
    const dx = (boundsX[1] - boundsX[0]) / nx; const dy = (boundsY[1] - boundsY[0]) / ny;
    const viscosity = scenario.foil.chord / scenario.reynolds; const decay = Math.exp(-2 * viscosity * scenario.duration); let squareError = 0;
    for (let y = 0; y < ny; y += 1) for (let x = 0; x < nx; x += 1) {
      const px = boundsX[0] + (x + 0.5) * dx; const py = boundsY[0] + (y + 0.5) * dy; const cell = y * nx + x;
      squareError += ((velocity[2 * cell] ?? 0) - Math.sin(px) * Math.cos(py) * decay) ** 2;
      squareError += ((velocity[2 * cell + 1] ?? 0) + Math.cos(px) * Math.sin(py) * decay) ** 2;
    }
    expect(Math.sqrt(squareError / velocity.length)).toBeLessThan(threshold(selected, "velocity_l2_error"));
    expect(advanced.diagnostics().values["kinetic_energy"] ?? 0).toBeLessThanOrEqual(before * threshold(selected, "kinetic_energy_ratio"));
  });

  for (const solverId of solverIds) it(`${solverId} retains the Poiseuille profile`, async () => {
    const {scenario, selected} = await scenarioFor("poiseuille"); const velocity = advance(solverId, scenario).exportState().velocity;
    const [nx, ny] = selected.resolution; const boundsY = scenario.domain.bounds[1]; if (boundsY === undefined) throw new RangeError("y bounds missing");
    const radius = 0.5 * (boundsY[1] - boundsY[0]); const centerY = 0.5 * (boundsY[0] + boundsY[1]); let squareError = 0; let leakage = 0;
    for (let y = 0; y < ny; y += 1) for (let x = 0; x < nx; x += 1) { const py = boundsY[0] + (y + 0.5) * (boundsY[1] - boundsY[0]) / ny; const expected = 1.5 * (1 - ((py - centerY) / radius) ** 2); const cell = y * nx + x; squareError += ((velocity[2 * cell] ?? 0) - expected) ** 2; if (y === 0 || y + 1 === ny) leakage = Math.max(leakage, Math.abs(velocity[2 * cell + 1] ?? 0)); }
    expect(Math.sqrt(squareError / (nx * ny))).toBeLessThan(threshold(selected, "profile_l2_error"));
    expect(leakage).toBeLessThan(threshold(selected, "normal_wall_leakage"));
  });

  for (const solverId of solverIds) it(`${solverId} keeps zero-angle NACA 0012 symmetric`, async () => {
    const {scenario, selected} = await scenarioFor("naca0012-zero"); const solver = advance(solverId, scenario); const velocity = solver.exportState().velocity;
    const [nx, ny] = selected.resolution; let squareError = 0;
    for (let y = 0; y < ny; y += 1) for (let x = 0; x < nx; x += 1) { const mirror = (ny - y - 1) * nx + x; const cell = y * nx + x; squareError += ((velocity[2 * cell] ?? 0) - (velocity[2 * mirror] ?? 0)) ** 2 + ((velocity[2 * cell + 1] ?? 0) + (velocity[2 * mirror + 1] ?? 0)) ** 2; }
    expect(Math.sqrt(squareError / velocity.length)).toBeLessThan(threshold(selected, "symmetry_l2_error"));
    expect(solver.diagnostics().values["solid_leakage"] ?? Number.POSITIVE_INFINITY).toBeLessThan(threshold(selected, "solid_leakage"));
  });

  for (const solverId of solverIds) it(`${solverId} reports finite dynamic NACA metrics`, async () => {
    const {scenario, selected} = await scenarioFor("naca2412-dynamic"); const values = advance(solverId, scenario).diagnostics().values;
    for (const metric of Object.keys(selected.metrics)) expect(Number.isFinite(Number(values[metric]))).toBe(true);
  });
});
