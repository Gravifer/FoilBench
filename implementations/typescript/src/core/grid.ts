import type {DomainSpec, FloatArray, Precision} from "./contracts.js";

export function allocate(precision: Precision, length: number): FloatArray {
  return precision === "float32" ? new Float32Array(length) : new Float64Array(length);
}

export function dimensions(domain: DomainSpec): {nx: number; ny: number; dx: number; dy: number} {
  const nx = domain.resolution[0]; const ny = domain.resolution[1];
  const bx = domain.bounds[0]; const by = domain.bounds[1];
  if (nx === undefined || ny === undefined || bx === undefined || by === undefined) throw new RangeError("2D domain is incomplete");
  return {nx, ny, dx: (bx[1] - bx[0]) / nx, dy: (by[1] - by[0]) / ny};
}

export function bounds2d(domain: DomainSpec): {x: readonly [number, number]; y: readonly [number, number]} {
  const x = domain.bounds[0]; const y = domain.bounds[1];
  if (x === undefined || y === undefined) throw new RangeError("2D bounds are incomplete");
  return {x, y};
}

export function cellVelocity(u: FloatArray, v: FloatArray, nx: number, ny: number, precision: Precision): FloatArray {
  const output = allocate(precision, nx * ny * 2);
  for (let y = 0; y < ny; y += 1) for (let x = 0; x < nx; x += 1) {
    const cell = y * nx + x;
    output[2 * cell] = 0.5 * ((u[y * (nx + 1) + x] ?? 0) + (u[y * (nx + 1) + x + 1] ?? 0));
    output[2 * cell + 1] = 0.5 * ((v[y * nx + x] ?? 0) + (v[(y + 1) * nx + x] ?? 0));
  }
  return output;
}

export function cellToFaces(velocity: FloatArray, nx: number, ny: number, precision: Precision, periodicX = false, periodicY = false): {u: FloatArray; v: FloatArray} {
  const u = allocate(precision, ny * (nx + 1)); const v = allocate(precision, (ny + 1) * nx);
  for (let y = 0; y < ny; y += 1) for (let x = 0; x <= nx; x += 1) {
    const left = periodicX ? (x - 1 + nx) % nx : Math.max(0, x - 1); const right = periodicX ? x % nx : Math.min(nx - 1, x);
    u[y * (nx + 1) + x] = 0.5 * ((velocity[2 * (y * nx + left)] ?? 0) + (velocity[2 * (y * nx + right)] ?? 0));
  }
  for (let y = 0; y <= ny; y += 1) for (let x = 0; x < nx; x += 1) {
    const bottom = periodicY ? (y - 1 + ny) % ny : Math.max(0, y - 1); const top = periodicY ? y % ny : Math.min(ny - 1, y);
    v[y * nx + x] = 0.5 * ((velocity[2 * (bottom * nx + x) + 1] ?? 0) + (velocity[2 * (top * nx + x) + 1] ?? 0));
  }
  return {u, v};
}

export function sampleCell(velocity: FloatArray, domain: DomainSpec, x: number, y: number): readonly [number, number] {
  const {nx, ny, dx, dy} = dimensions(domain); const {x: bx, y: by} = bounds2d(domain);
  const periodicX = domain.periodicAxes.includes("x"); const periodicY = domain.periodicAxes.includes("y");
  const rawX = (x - bx[0]) / dx - 0.5; const rawY = (y - by[0]) / dy - 0.5;
  const gx = periodicX ? ((rawX % nx) + nx) % nx : Math.max(0, Math.min(nx - 1, rawX));
  const gy = periodicY ? ((rawY % ny) + ny) % ny : Math.max(0, Math.min(ny - 1, rawY));
  const x0 = Math.floor(gx); const y0 = Math.floor(gy); const x1 = periodicX ? (x0 + 1) % nx : Math.min(nx - 1, x0 + 1); const y1 = periodicY ? (y0 + 1) % ny : Math.min(ny - 1, y0 + 1);
  const tx = gx - x0; const ty = gy - y0;
  const component = (c: number): number => {
    const a = velocity[2 * (y0 * nx + x0) + c] ?? 0; const b = velocity[2 * (y0 * nx + x1) + c] ?? 0;
    const d = velocity[2 * (y1 * nx + x0) + c] ?? 0; const e = velocity[2 * (y1 * nx + x1) + c] ?? 0;
    return (1 - ty) * ((1 - tx) * a + tx * b) + ty * ((1 - tx) * d + tx * e);
  };
  return [component(0), component(1)];
}

export function divergence(u: FloatArray, v: FloatArray, nx: number, ny: number, dx: number, dy: number, precision: Precision): FloatArray {
  const output = allocate(precision, nx * ny);
  for (let y = 0; y < ny; y += 1) for (let x = 0; x < nx; x += 1) output[y * nx + x] = ((u[y * (nx + 1) + x + 1] ?? 0) - (u[y * (nx + 1) + x] ?? 0)) / dx + ((v[(y + 1) * nx + x] ?? 0) - (v[y * nx + x] ?? 0)) / dy;
  return output;
}

export interface ProjectionReport {
  readonly criterion: "pressure-change-linf";
  readonly tolerance: number;
  readonly iterations: number;
  readonly finalResidual: number;
  readonly relativeResidual: number;
  readonly divergenceLinf: number;
  readonly converged: boolean;
}

export function project(u: FloatArray, v: FloatArray, solid: Uint8Array, nx: number, ny: number, dx: number, dy: number, precision: Precision, iterations: number, tolerance: number, periodicX = false, periodicY = false): ProjectionReport {
  const rhs = divergence(u, v, nx, ny, dx, dy, precision); const pressure = allocate(precision, nx * ny); const next = allocate(precision, nx * ny);
  const invDx2 = 1 / (dx * dx); const invDy2 = 1 / (dy * dy);
  let fluidCount = 0; let rhsMean = 0; for (let index = 0; index < rhs.length; index += 1) if (solid[index] === 0) { rhsMean += rhs[index] ?? 0; fluidCount += 1; } rhsMean /= Math.max(1, fluidCount); for (let index = 0; index < rhs.length; index += 1) if (solid[index] === 0) rhs[index] = (rhs[index] ?? 0) - rhsMean;
  let performed = 0; let finalChange = Number.POSITIVE_INFINITY; let converged = false;
  for (let iteration = 0; iteration < iterations; iteration += 1) {
    let maxChange = 0;
    for (let y = 0; y < ny; y += 1) for (let x = 0; x < nx; x += 1) {
      const index = y * nx + x;
      if (solid[index] !== 0) { next[index] = 0; continue; }
      let sum = 0; let weight = 0;
      const left = x > 0 ? index - 1 : periodicX ? index + nx - 1 : -1; const right = x + 1 < nx ? index + 1 : periodicX ? index - nx + 1 : -1;
      const bottom = y > 0 ? index - nx : periodicY ? index + nx * (ny - 1) : -1; const top = y + 1 < ny ? index + nx : periodicY ? index - nx * (ny - 1) : -1;
      if (left >= 0 && solid[left] === 0) { sum += (pressure[left] ?? 0) * invDx2; weight += invDx2; }
      if (right >= 0 && solid[right] === 0) { sum += (pressure[right] ?? 0) * invDx2; weight += invDx2; }
      if (bottom >= 0 && solid[bottom] === 0) { sum += (pressure[bottom] ?? 0) * invDy2; weight += invDy2; }
      if (top >= 0 && solid[top] === 0) { sum += (pressure[top] ?? 0) * invDy2; weight += invDy2; }
      const value = weight > 0 ? (sum - (rhs[index] ?? 0)) / weight : 0;
      maxChange = Math.max(maxChange, Math.abs(value - (pressure[index] ?? 0))); next[index] = value;
    }
    pressure.set(next); performed = iteration + 1; finalChange = maxChange; if (maxChange < tolerance) { converged = true; break; }
  }
  for (let y = 0; y < ny; y += 1) for (let x = 1; x < nx; x += 1) if (solid[y * nx + x - 1] === 0 && solid[y * nx + x] === 0) u[y * (nx + 1) + x] = (u[y * (nx + 1) + x] ?? 0) - ((pressure[y * nx + x] ?? 0) - (pressure[y * nx + x - 1] ?? 0)) / dx;
  for (let y = 1; y < ny; y += 1) for (let x = 0; x < nx; x += 1) if (solid[(y - 1) * nx + x] === 0 && solid[y * nx + x] === 0) v[y * nx + x] = (v[y * nx + x] ?? 0) - ((pressure[y * nx + x] ?? 0) - (pressure[(y - 1) * nx + x] ?? 0)) / dy;
  if (periodicX) for (let y = 0; y < ny; y += 1) { const left = y * nx + nx - 1; const right = y * nx; if (solid[left] === 0 && solid[right] === 0) { const value = (u[y * (nx + 1)] ?? 0) - ((pressure[right] ?? 0) - (pressure[left] ?? 0)) / dx; u[y * (nx + 1)] = value; u[y * (nx + 1) + nx] = value; } }
  if (periodicY) for (let x = 0; x < nx; x += 1) { const bottom = (ny - 1) * nx + x; const top = x; if (solid[bottom] === 0 && solid[top] === 0) { const value = (v[x] ?? 0) - ((pressure[top] ?? 0) - (pressure[bottom] ?? 0)) / dy; v[x] = value; v[ny * nx + x] = value; } }
  let residualSquared = 0; let rhsSquared = 0;
  for (let y = 0; y < ny; y += 1) for (let x = 0; x < nx; x += 1) {
    const index = y * nx + x; if (solid[index] !== 0) continue; let sum = 0; let weight = 0;
    const left = x > 0 ? index - 1 : periodicX ? index + nx - 1 : -1; const right = x + 1 < nx ? index + 1 : periodicX ? index - nx + 1 : -1; const bottom = y > 0 ? index - nx : periodicY ? index + nx * (ny - 1) : -1; const top = y + 1 < ny ? index + nx : periodicY ? index - nx * (ny - 1) : -1;
    if (left >= 0 && solid[left] === 0) { sum += (pressure[left] ?? 0) * invDx2; weight += invDx2; } if (right >= 0 && solid[right] === 0) { sum += (pressure[right] ?? 0) * invDx2; weight += invDx2; } if (bottom >= 0 && solid[bottom] === 0) { sum += (pressure[bottom] ?? 0) * invDy2; weight += invDy2; } if (top >= 0 && solid[top] === 0) { sum += (pressure[top] ?? 0) * invDy2; weight += invDy2; }
    const residual = sum - weight * (pressure[index] ?? 0) - (rhs[index] ?? 0); residualSquared += residual * residual; rhsSquared += (rhs[index] ?? 0) ** 2;
  }
  const projectedDivergence = divergence(u, v, nx, ny, dx, dy, precision); let divergenceLinf = 0; for (let index = 0; index < projectedDivergence.length; index += 1) if (solid[index] === 0) divergenceLinf = Math.max(divergenceLinf, Math.abs(projectedDivergence[index] ?? 0)); const finalResidual = Math.sqrt(residualSquared); const epsilon = precision === "float32" ? 1e-7 : 1e-15;
  return {criterion: "pressure-change-linf", tolerance, iterations: performed, finalResidual, relativeResidual: finalResidual / Math.max(Math.sqrt(rhsSquared), epsilon), divergenceLinf, converged: converged && Number.isFinite(finalChange)};
}
