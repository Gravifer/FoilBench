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
  readonly criterion: "relative-residual-l2";
  readonly tolerance: number;
  readonly iterations: number;
  readonly finalResidual: number;
  readonly relativeResidual: number;
  readonly divergenceLinf: number;
  readonly converged: boolean;
}

export function project(u: FloatArray, v: FloatArray, solid: Uint8Array, nx: number, ny: number, dx: number, dy: number, precision: Precision, iterations: number, tolerance: number, periodicX = false, periodicY = false): ProjectionReport {
  const divergenceBefore = divergence(u, v, nx, ny, dx, dy, precision);
  // Retain the requested storage precision for velocity, but perform the
  // pressure iteration in Float64 so the independently recomputed residual is
  // not limited by recursive Float32 CG drift.
  const solvePrecision: Precision = "float64";
  const pressure = allocate(solvePrecision, nx * ny); const rightHandSide = allocate(solvePrecision, nx * ny); const diagonal = allocate(solvePrecision, nx * ny);
  const invDx2 = 1 / (dx * dx); const invDy2 = 1 / (dy * dy);
  let fluidCount = 0; let rightMean = 0;
  for (let y = 0; y < ny; y += 1) for (let x = 0; x < nx; x += 1) {
    const index = y * nx + x; if (solid[index] !== 0) { diagonal[index] = 1; rightHandSide[index] = 0; continue; }
    fluidCount += 1; const right = -(divergenceBefore[index] ?? 0); rightHandSide[index] = right; rightMean += right;
    let value = 0;
    const left = x > 0 ? index - 1 : periodicX ? index + nx - 1 : -1; const rightIndex = x + 1 < nx ? index + 1 : periodicX ? index - nx + 1 : -1;
    const bottom = y > 0 ? index - nx : periodicY ? index + nx * (ny - 1) : -1; const top = y + 1 < ny ? index + nx : periodicY ? index - nx * (ny - 1) : -1;
    if (left >= 0) { if (solid[left] === 0) value += invDx2; } else value += invDx2;
    if (rightIndex >= 0) { if (solid[rightIndex] === 0) value += invDx2; } else value += invDx2;
    if (bottom >= 0) { if (solid[bottom] === 0) value += invDy2; } else value += invDy2;
    if (top >= 0) { if (solid[top] === 0) value += invDy2; } else value += invDy2;
    diagonal[index] = Math.max(value, precision === "float32" ? 1e-7 : 1e-15);
  }
  if (periodicX && periodicY && fluidCount > 0) { rightMean /= fluidCount; for (let index = 0; index < rightHandSide.length; index += 1) if (solid[index] === 0) rightHandSide[index] = (rightHandSide[index] ?? 0) - rightMean; }
  const applyOperator = (source: FloatArray, destination: FloatArray): void => {
    for (let y = 0; y < ny; y += 1) for (let x = 0; x < nx; x += 1) {
      const index = y * nx + x; if (solid[index] !== 0) { destination[index] = source[index] ?? 0; continue; }
      let value = (diagonal[index] ?? 0) * (source[index] ?? 0);
      const left = x > 0 ? index - 1 : periodicX ? index + nx - 1 : -1; const right = x + 1 < nx ? index + 1 : periodicX ? index - nx + 1 : -1;
      const bottom = y > 0 ? index - nx : periodicY ? index + nx * (ny - 1) : -1; const top = y + 1 < ny ? index + nx : periodicY ? index - nx * (ny - 1) : -1;
      if (left >= 0 && solid[left] === 0) value -= invDx2 * (source[left] ?? 0);
      if (right >= 0 && solid[right] === 0) value -= invDx2 * (source[right] ?? 0);
      if (bottom >= 0 && solid[bottom] === 0) value -= invDy2 * (source[bottom] ?? 0);
      if (top >= 0 && solid[top] === 0) value -= invDy2 * (source[top] ?? 0);
      destination[index] = value;
    }
  };
  const residual = rightHandSide.slice(); const preconditioned = allocate(solvePrecision, nx * ny); const preconditionerScratch = allocate(solvePrecision, nx * ny); const direction = allocate(solvePrecision, nx * ny); const operatorDirection = allocate(solvePrecision, nx * ny);
  const applyPreconditioner = (source: FloatArray, destination: FloatArray): void => {
    for (let y = 0; y < ny; y += 1) for (let x = 0; x < nx; x += 1) { const index = y * nx + x; if (solid[index] !== 0) { preconditionerScratch[index] = source[index] ?? 0; continue; } let value = source[index] ?? 0; if (x > 0 && solid[index - 1] === 0) value += invDx2 * (preconditionerScratch[index - 1] ?? 0); if (y > 0 && solid[index - nx] === 0) value += invDy2 * (preconditionerScratch[index - nx] ?? 0); preconditionerScratch[index] = value / Math.max(diagonal[index] ?? 0, epsilon); }
    for (let y = ny - 1; y >= 0; y -= 1) for (let x = nx - 1; x >= 0; x -= 1) { const index = y * nx + x; if (solid[index] !== 0) { destination[index] = preconditionerScratch[index] ?? 0; continue; } let value = (diagonal[index] ?? 0) * (preconditionerScratch[index] ?? 0); if (x + 1 < nx && solid[index + 1] === 0) value += invDx2 * (destination[index + 1] ?? 0); if (y + 1 < ny && solid[index + nx] === 0) value += invDy2 * (destination[index + nx] ?? 0); destination[index] = value / Math.max(diagonal[index] ?? 0, epsilon); }
  };
  const epsilon = 1e-15; applyPreconditioner(residual, preconditioned);
  let rightNormSquared = 0; let residualDot = 0; for (let index = 0; index < residual.length; index += 1) { const right = rightHandSide[index] ?? 0; direction[index] = preconditioned[index] ?? 0; rightNormSquared += right * right; residualDot += (residual[index] ?? 0) * (preconditioned[index] ?? 0); }
  // The recursively accumulated CG residual can be slightly more optimistic
  // than the independently recomputed contract residual, especially in
  // Float32.  Leave the same deliberate margin as the Python reference so an
  // apparently converged iteration does not fail solely at final validation.
  const convergenceTarget = tolerance * 0.9;
  const rightNorm = Math.sqrt(rightNormSquared); let performed = 0; let relativeResidual = rightNorm <= epsilon ? 0 : 1; let converged = rightNorm <= epsilon;
  for (let iteration = 0; iteration < iterations && !converged; iteration += 1) {
    applyOperator(direction, operatorDirection); let denominator = 0; for (let index = 0; index < direction.length; index += 1) denominator += (direction[index] ?? 0) * (operatorDirection[index] ?? 0);
    if (!(Number.isFinite(denominator) && denominator > 0 && Number.isFinite(residualDot))) break;
    const alpha = residualDot / denominator; let residualNormSquared = 0;
    for (let index = 0; index < pressure.length; index += 1) { pressure[index] = (pressure[index] ?? 0) + alpha * (direction[index] ?? 0); residual[index] = (residual[index] ?? 0) - alpha * (operatorDirection[index] ?? 0); residualNormSquared += (residual[index] ?? 0) ** 2; }
    performed = iteration + 1; relativeResidual = Math.sqrt(residualNormSquared) / Math.max(rightNorm, epsilon); if (relativeResidual <= convergenceTarget) { converged = true; break; }
    applyPreconditioner(residual, preconditioned); let nextResidualDot = 0; for (let index = 0; index < residual.length; index += 1) nextResidualDot += (residual[index] ?? 0) * (preconditioned[index] ?? 0);
    if (!(Number.isFinite(nextResidualDot) && residualDot !== 0)) break; const beta = nextResidualDot / residualDot; for (let index = 0; index < direction.length; index += 1) direction[index] = (preconditioned[index] ?? 0) + beta * (direction[index] ?? 0); residualDot = nextResidualDot;
  }
  if (periodicX && periodicY && fluidCount > 0) { let mean = 0; for (let index = 0; index < pressure.length; index += 1) if (solid[index] === 0) mean += pressure[index] ?? 0; mean /= fluidCount; for (let index = 0; index < pressure.length; index += 1) if (solid[index] === 0) pressure[index] = (pressure[index] ?? 0) - mean; }
  for (let y = 0; y < ny; y += 1) for (let x = 1; x < nx; x += 1) if (solid[y * nx + x - 1] === 0 && solid[y * nx + x] === 0) u[y * (nx + 1) + x] = (u[y * (nx + 1) + x] ?? 0) - ((pressure[y * nx + x] ?? 0) - (pressure[y * nx + x - 1] ?? 0)) / dx;
  for (let y = 1; y < ny; y += 1) for (let x = 0; x < nx; x += 1) if (solid[(y - 1) * nx + x] === 0 && solid[y * nx + x] === 0) v[y * nx + x] = (v[y * nx + x] ?? 0) - ((pressure[y * nx + x] ?? 0) - (pressure[(y - 1) * nx + x] ?? 0)) / dy;
  if (periodicX) for (let y = 0; y < ny; y += 1) { const left = y * nx + nx - 1; const right = y * nx; if (solid[left] === 0 && solid[right] === 0) { const value = (u[y * (nx + 1)] ?? 0) - ((pressure[right] ?? 0) - (pressure[left] ?? 0)) / dx; u[y * (nx + 1)] = value; u[y * (nx + 1) + nx] = value; } }
  if (periodicY) for (let x = 0; x < nx; x += 1) { const bottom = (ny - 1) * nx + x; const top = x; if (solid[bottom] === 0 && solid[top] === 0) { const value = (v[x] ?? 0) - ((pressure[top] ?? 0) - (pressure[bottom] ?? 0)) / dy; v[x] = value; v[ny * nx + x] = value; } }
  applyOperator(pressure, operatorDirection); let finalResidualSquared = 0; for (let index = 0; index < operatorDirection.length; index += 1) { const value = (rightHandSide[index] ?? 0) - (operatorDirection[index] ?? 0); finalResidualSquared += value * value; } const finalResidual = Math.sqrt(finalResidualSquared); relativeResidual = finalResidual / Math.max(rightNorm, epsilon); converged = converged && Number.isFinite(relativeResidual) && relativeResidual <= tolerance;
  const projectedDivergence = divergence(u, v, nx, ny, dx, dy, precision); let divergenceLinf = 0; for (let index = 0; index < projectedDivergence.length; index += 1) if (solid[index] === 0) divergenceLinf = Math.max(divergenceLinf, Math.abs(projectedDivergence[index] ?? 0));
  return {criterion: "relative-residual-l2", tolerance, iterations: performed, finalResidual, relativeResidual, divergenceLinf, converged};
}
