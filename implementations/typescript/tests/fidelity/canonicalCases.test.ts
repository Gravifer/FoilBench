import {readFile} from "node:fs/promises";
import {resolve} from "node:path";
import {describe, expect, it} from "vitest";
import type {FloatArray, Scenario, SolverId} from "../../src/core/contracts.js";
import {controlAt, parseScenario} from "../../src/core/scenario.js";
import {createSolver} from "../../src/solvers/factory.js";

const solverIds: readonly SolverId[] = ["stable-fluids", "lbm-d2q9", "pic-flip"];

async function validationScenario(name: string, resolution: readonly [number, number], duration: number): Promise<Scenario> {
  const schema = JSON.parse(await readFile(resolve("../../spec/schemas/scenario.schema.json"), "utf8")) as object;
  const document = JSON.parse(await readFile(resolve(`../../scenarios/validation/${name}`), "utf8")) as unknown;
  const scenario = parseScenario(document, schema);
  return {...scenario, domain: {...scenario.domain, resolution}, duration};
}

function rmsDifference(left: FloatArray, right: FloatArray): number {
  let total = 0; for (let index = 0; index < left.length; index += 1) total += ((left[index] ?? 0) - (right[index] ?? 0)) ** 2;
  return Math.sqrt(total / left.length);
}

function advance(solverId: SolverId, scenario: Scenario, steps: number): ReturnType<typeof createSolver> {
  const solver = createSolver(solverId); solver.initialize(scenario, scenario.seed);
  for (let step = 1; step <= steps; step += 1) solver.advance(controlAt(scenario, step * scenario.outputDt), scenario.outputDt);
  return solver;
}

describe("matched canonical fidelity cases", () => {
  for (const solverId of solverIds) it(`${solverId} preserves uniform flow`, async () => {
    const scenario = await validationScenario("uniform.json", [32, 16], 0.1);
    const solver = createSolver(solverId); solver.initialize(scenario, 0); const before = solver.exportState().velocity.slice();
    for (let step = 1; step <= 5; step += 1) solver.advance(controlAt(scenario, step * scenario.outputDt), scenario.outputDt);
    expect(rmsDifference(solver.exportState().velocity, before)).toBeLessThan(1e-5);
    expect(solver.diagnostics().values["enstrophy"]).toBeLessThan(1e-5);
  });

  for (const solverId of solverIds) it(`${solverId} retains the Taylor-Green structure`, async () => {
    const scenario = await validationScenario("taylor-green.json", [32, 32], 0.1);
    const solver = createSolver(solverId); solver.initialize(scenario, 0); const initialEnergy = solver.diagnostics().values["kinetic_energy"] ?? 0;
    for (let step = 1; step <= 5; step += 1) solver.advance(controlAt(scenario, step * scenario.outputDt), scenario.outputDt);
    const actual = solver.exportState().velocity; const expected = new Float64Array(actual.length); const [[x0, x1], [y0, y1]] = scenario.domain.bounds as readonly [readonly [number, number], readonly [number, number]]; const [nx, ny] = scenario.domain.resolution as readonly [number, number]; const decay = Math.exp(-2 * scenario.foil.chord / scenario.reynolds * scenario.duration);
    for (let y = 0; y < ny; y += 1) for (let x = 0; x < nx; x += 1) { const px = x0 + (x + 0.5) * (x1 - x0) / nx; const py = y0 + (y + 0.5) * (y1 - y0) / ny; const cell = y * nx + x; expected[2 * cell] = Math.sin(px) * Math.cos(py) * decay; expected[2 * cell + 1] = -Math.cos(px) * Math.sin(py) * decay; }
    expect(rmsDifference(actual, expected)).toBeLessThan(0.08);
    expect(solver.diagnostics().values["kinetic_energy"]).toBeLessThanOrEqual(initialEnergy * 1.01);
  });

  for (const solverId of solverIds) it(`${solverId} retains the Poiseuille profile without wall-normal leakage`, async () => {
    const scenario = await validationScenario("poiseuille.json", [64, 32], 0.2); const solver = advance(solverId, scenario, 10); const velocity = solver.exportState().velocity; const [nx, ny] = scenario.domain.resolution as readonly [number, number]; const [[x0, x1], [y0, y1]] = scenario.domain.bounds as readonly [readonly [number, number], readonly [number, number]]; void x0; void x1; const radius = 0.5 * (y1 - y0); const centerY = 0.5 * (y0 + y1); let center = 0; let walls = 0; let squareError = 0; let normalLeakage = 0;
    for (let y = 0; y < ny; y += 1) for (let x = 0; x < nx; x += 1) { const py = y0 + (y + 0.5) * (y1 - y0) / ny; const expected = 1.5 * (1 - ((py - centerY) / radius) ** 2); const cell = y * nx + x; squareError += ((velocity[2 * cell] ?? 0) - expected) ** 2; if (y === 0 || y + 1 === ny) normalLeakage = Math.max(normalLeakage, Math.abs(velocity[2 * cell + 1] ?? 0)); }
    for (let x = 0; x < nx; x += 1) { center += velocity[2 * (Math.floor(ny / 2) * nx + x)] ?? 0; walls += 0.5 * ((velocity[2 * x] ?? 0) + (velocity[2 * ((ny - 1) * nx + x)] ?? 0)); }
    expect(center / nx).toBeGreaterThan(walls / nx);
    expect(Math.sqrt(squareError / (nx * ny))).toBeLessThan(0.25);
    expect(normalLeakage).toBeLessThan(0.01);
  });

  for (const solverId of solverIds) it(`${solverId} keeps zero-angle NACA 0012 symmetric and impenetrable`, async () => {
    const scenario = await validationScenario("naca0012-zero.json", [80, 48], 0.5); const solver = advance(solverId, scenario, 30); const velocity = solver.exportState().velocity; const [nx, ny] = scenario.domain.resolution as readonly [number, number]; let squareError = 0;
    for (let y = 0; y < ny; y += 1) for (let x = 0; x < nx; x += 1) { const mirror = (ny - y - 1) * nx + x; const cell = y * nx + x; squareError += ((velocity[2 * cell] ?? 0) - (velocity[2 * mirror] ?? 0)) ** 2 + ((velocity[2 * cell + 1] ?? 0) + (velocity[2 * mirror + 1] ?? 0)) ** 2; }
    expect(Math.sqrt(squareError / velocity.length)).toBeLessThan(0.01);
    expect(solver.diagnostics().values["solid_leakage"]).toBeLessThan(1e-6);
  });

  for (const solverId of solverIds) it(`${solverId} reports finite dynamic NACA 2412 metrics`, async () => {
    const schema = JSON.parse(await readFile(resolve("../../spec/schemas/scenario.schema.json"), "utf8")) as object; const document = JSON.parse(await readFile(resolve("../../scenarios/airfoil/default.json"), "utf8")) as unknown; const loaded = parseScenario(document, schema); const scenario = {...loaded, domain: {...loaded.domain, resolution: [32, 20]}, duration: 0.05}; const diagnostics = advance(solverId, scenario, 3).diagnostics().values;
    for (const name of ["wake_width", "recirculation_area", "enstrophy", "solid_leakage"] as const) expect(Number.isFinite(diagnostics[name])).toBe(true);
  });
});
