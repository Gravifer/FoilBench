import type {DomainSpec, FloatArray, Scenario} from "./contracts.js";
import type {NacaFoil} from "./geometry.js";
import {bounds2d, dimensions} from "./grid.js";

export function wakeMetrics(
  velocity: FloatArray,
  domain: DomainSpec,
  pivotX: number,
  chord: number,
  freestreamU: number,
  solid: Uint8Array,
): Readonly<{wake_width: number; recirculation_area: number}> {
  const {nx, ny, dx, dy} = dimensions(domain);
  const {x: bx} = bounds2d(domain);
  const activeRows = new Uint8Array(ny);
  let recirculatingCells = 0;
  for (let y = 0; y < ny; y += 1) for (let x = 0; x < nx; x += 1) {
    const cell = y * nx + x;
    if (solid[cell] !== 0) continue;
    const px = bx[0] + (x + 0.5) * dx;
    const u = velocity[2 * cell] ?? 0;
    if (px > pivotX && u < 0) recirculatingCells += 1;
    if (px > pivotX + chord && freestreamU - u > 0.1 * Math.abs(freestreamU)) activeRows[y] = 1;
  }
  let activeRowCount = 0;
  for (const active of activeRows) activeRowCount += active;
  return {
    wake_width: activeRowCount * dy,
    recirculation_area: recirculatingCells * dx * dy,
  };
}

export function fieldDiagnostics(
  velocity: FloatArray,
  scenario: Scenario,
  foil: NacaFoil,
  angleDegrees: number,
): Readonly<Record<string, number>> {
  const {nx, ny, dx, dy} = dimensions(scenario.domain);
  const {x: bx, y: by} = bounds2d(scenario.domain);
  let kineticEnergy = 0;
  let enstrophy = 0;
  let divergenceLinf = 0;
  let solidLeakage = 0;
  const solid = new Uint8Array(nx * ny);
  for (let y = 0; y < ny; y += 1) for (let x = 0; x < nx; x += 1) {
    const cell = y * nx + x;
    const u = velocity[2 * cell] ?? 0;
    const v = velocity[2 * cell + 1] ?? 0;
    kineticEnergy += 0.5 * (u * u + v * v);
    const px = bx[0] + (x + 0.5) * dx;
    const py = by[0] + (y + 0.5) * dy;
    if (foil.signedDistance(px, py, angleDegrees) <= 0) {
      solid[cell] = 1;
      solidLeakage = Math.max(solidLeakage, Math.hypot(u, v));
    }
    if (x === 0 || x + 1 === nx || y === 0 || y + 1 === ny) continue;
    const left = y * nx + x - 1; const right = y * nx + x + 1;
    const bottom = (y - 1) * nx + x; const top = (y + 1) * nx + x;
    const dvdx = ((velocity[2 * right + 1] ?? 0) - (velocity[2 * left + 1] ?? 0)) / (2 * dx);
    const dudy = ((velocity[2 * top] ?? 0) - (velocity[2 * bottom] ?? 0)) / (2 * dy);
    const dudx = ((velocity[2 * right] ?? 0) - (velocity[2 * left] ?? 0)) / (2 * dx);
    const dvdy = ((velocity[2 * top + 1] ?? 0) - (velocity[2 * bottom + 1] ?? 0)) / (2 * dy);
    const vorticity = dvdx - dudy;
    enstrophy += 0.5 * vorticity * vorticity;
    divergenceLinf = Math.max(divergenceLinf, Math.abs(dudx + dvdy));
  }
  const cells = nx * ny;
  const wake = wakeMetrics(
    velocity,
    scenario.domain,
    scenario.foil.pivot[0] ?? 0,
    scenario.foil.chord,
    scenario.freestream[0] ?? 0,
    solid,
  );
  return {
    kinetic_energy: kineticEnergy / cells,
    energy: kineticEnergy / cells,
    enstrophy: enstrophy / cells,
    reconstructed_divergence_linf: divergenceLinf,
    solid_cell_speed: solidLeakage,
    ...wake,
  };
}
