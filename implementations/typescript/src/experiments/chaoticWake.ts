import type {FloatArray, Scenario} from "../core/contracts.js";
import {NacaFoil} from "../core/geometry.js";
import {bounds2d, dimensions} from "../core/grid.js";
import {controlAt} from "../core/scenario.js";
import {createSolver} from "../solvers/factory.js";

export interface WakeCase {readonly reynolds: number; readonly angleDegrees: number; readonly resolution: readonly [number, number]}
export interface ExperimentEnvelope {
  readonly schema_version: 1; readonly contract_id: "foilbench-phase2-v1";
  readonly experiment: "chaotic-wake-sweep" | "chaotic-wake-sensitivity";
  readonly language: "typescript"; readonly solver: "stable-fluids"; readonly scenario: string;
  readonly parameters: Readonly<Record<string, number | readonly number[]>>;
  readonly metrics: Readonly<Record<string, number>>; readonly series?: {readonly times: readonly number[]; readonly wake_rms_differences: readonly number[]};
  readonly wall_seconds: number;
}

export function chaoticScenario(base: Scenario, selected: WakeCase, duration: number): Scenario {
  return {
    ...base,
    id: `chaotic-wake-re${String(selected.reynolds)}-a${String(selected.angleDegrees)}-${String(selected.resolution[0])}x${String(selected.resolution[1])}`,
    domain: {...base.domain, resolution: selected.resolution}, reynolds: selected.reynolds,
    controls: [{time: 0, angleDegrees: selected.angleDegrees}, {time: duration, angleDegrees: selected.angleDegrees}],
    duration, solverOptions: {...base.solverOptions, stableAdvection: "skew-rk2"},
  };
}

function spectrum(samples: readonly number[]): {entropy: number; dominant: number; broadband: number} {
  if (samples.length < 4) return {entropy: 0, dominant: 0, broadband: 0};
  const mean = samples.reduce((sum, value) => sum + value, 0) / samples.length;
  const powers = new Float64Array(Math.floor(samples.length / 2) + 1);
  for (let frequency = 1; frequency < powers.length; frequency += 1) {
    let real = 0; let imaginary = 0;
    for (let index = 0; index < samples.length; index += 1) {
      const window = 0.5 - 0.5 * Math.cos(2 * Math.PI * index / (samples.length - 1));
      const value = ((samples[index] ?? 0) - mean) * window; const phase = -2 * Math.PI * frequency * index / samples.length;
      real += value * Math.cos(phase); imaginary += value * Math.sin(phase);
    }
    powers[frequency] = real * real + imaginary * imaginary;
  }
  const total = powers.reduce((sum, value) => sum + value, 0); if (total <= Number.EPSILON) return {entropy: 0, dominant: 0, broadband: 0};
  let entropy = 0; let dominantIndex = 1;
  for (let index = 1; index < powers.length; index += 1) { const probability = (powers[index] ?? 0) / total; if (probability > 0) entropy -= probability * Math.log(probability); if ((powers[index] ?? 0) > (powers[dominantIndex] ?? 0)) dominantIndex = index; }
  entropy /= Math.log(Math.max(2, powers.length - 1)); let coherent = 0;
  for (let index = Math.max(1, dominantIndex - 1); index <= Math.min(powers.length - 1, dominantIndex + 1); index += 1) coherent += powers[index] ?? 0;
  return {entropy, dominant: (powers[dominantIndex] ?? 0) / total, broadband: 1 - coherent / total};
}

function decorrelationTime(samples: readonly number[], dt: number): number {
  if (samples.length === 0) return 0; const mean = samples.reduce((sum, value) => sum + value, 0) / samples.length; const centered = samples.map((value) => value - mean); const variance = centered.reduce((sum, value) => sum + value * value, 0) / samples.length; if (variance <= Number.EPSILON) return 0;
  for (let lag = 0; lag < samples.length; lag += 1) { let correlation = 0; const overlap = samples.length - lag; for (let index = 0; index < overlap; index += 1) correlation += (centered[index] ?? 0) * (centered[index + lag] ?? 0); if (correlation / (overlap * variance) < Math.exp(-1)) return lag * dt; }
  return (samples.length - 1) * dt;
}

function vorticitySmallScaleFraction(velocity: FloatArray, scenario: Scenario): number {
  const {nx, ny, dx, dy} = dimensions(scenario.domain); const omega = new Float64Array(nx * ny); let total = 0;
  for (let y = 1; y + 1 < ny; y += 1) for (let x = 1; x + 1 < nx; x += 1) { const left = y * nx + x - 1; const right = left + 2; const bottom = (y - 1) * nx + x; const top = (y + 1) * nx + x; const value = ((velocity[2 * right + 1] ?? 0) - (velocity[2 * left + 1] ?? 0)) / (2 * dx) - ((velocity[2 * top] ?? 0) - (velocity[2 * bottom] ?? 0)) / (2 * dy); omega[y * nx + x] = value; total += value * value; }
  if (total <= Number.EPSILON) return 0; let gradient = 0;
  for (let y = 2; y + 2 < ny; y += 1) for (let x = 2; x + 2 < nx; x += 1) { const gx = 0.5 * ((omega[y * nx + x + 1] ?? 0) - (omega[y * nx + x - 1] ?? 0)); const gy = 0.5 * ((omega[(y + 1) * nx + x] ?? 0) - (omega[(y - 1) * nx + x] ?? 0)); gradient += gx * gx + gy * gy; }
  return gradient / total;
}

export function runChaoticWakeCase(base: Scenario, selected: WakeCase, duration = 12, burnIn = 4): ExperimentEnvelope {
  if (!(burnIn >= 0 && burnIn < duration)) throw new RangeError("burn-in must lie inside the run");
  const scenario = chaoticScenario(base, selected, duration); const solver = createSolver("stable-fluids"); solver.initialize(scenario, scenario.seed);
  const probe = new Float32Array([(scenario.foil.pivot[0] ?? 0) + 1.5 * scenario.foil.chord, scenario.foil.pivot[1] ?? 0]); const transverse: number[] = []; const enstrophy: number[] = []; let maximumSpeed = 0; let simulated = 0; const started = performance.now();
  while (simulated < duration - 1e-12) { const dt = Math.min(scenario.outputDt, duration - simulated); simulated += dt; const report = solver.advance(controlAt(scenario, simulated), dt); maximumSpeed = Math.max(maximumSpeed, report.maxSpeed); if (simulated >= burnIn) { transverse.push(solver.sampleVelocity(probe)[1] ?? 0); enstrophy.push(solver.diagnostics().values["enstrophy"] ?? 0); } }
  const selectedSpectrum = spectrum(transverse); const probeMean = transverse.reduce((sum, value) => sum + value, 0) / Math.max(1, transverse.length); const enstrophyMean = enstrophy.reduce((sum, value) => sum + value, 0) / Math.max(1, enstrophy.length); const enstrophyVariance = enstrophy.reduce((sum, value) => sum + (value - enstrophyMean) ** 2, 0) / Math.max(1, enstrophy.length);
  return {schema_version: 1, contract_id: "foilbench-phase2-v1", experiment: "chaotic-wake-sweep", language: "typescript", solver: "stable-fluids", scenario: scenario.id, parameters: {reynolds: selected.reynolds, angle_degrees: selected.angleDegrees, resolution: selected.resolution, duration, burn_in: burnIn}, metrics: {probe_rms: Math.sqrt(transverse.reduce((sum, value) => sum + (value - probeMean) ** 2, 0) / Math.max(1, transverse.length)), spectral_entropy: selectedSpectrum.entropy, dominant_power_fraction: selectedSpectrum.dominant, broadband_power_fraction: selectedSpectrum.broadband, decorrelation_time: decorrelationTime(transverse, scenario.outputDt), enstrophy_mean: enstrophyMean, enstrophy_coefficient_of_variation: Math.sqrt(enstrophyVariance) / Math.max(enstrophyMean, Number.EPSILON), maximum_speed: maximumSpeed, vorticity_small_scale_fraction: vorticitySmallScaleFraction(solver.exportState().velocity, scenario)}, wall_seconds: (performance.now() - started) / 1000};
}

function wakeDifference(first: FloatArray, second: FloatArray, wake: Uint8Array): number { let sum = 0; let samples = 0; for (let cell = 0; cell < wake.length; cell += 1) if (wake[cell] !== 0) for (let component = 0; component < 2; component += 1) { const difference = (first[2 * cell + component] ?? 0) - (second[2 * cell + component] ?? 0); sum += difference * difference; samples += 1; } return Math.sqrt(sum / Math.max(samples, 1)); }

function exponentialFit(times: readonly number[], differences: readonly number[], initial: number): readonly [number, number, number] {
  const selected = times.map((_, index) => index).filter((index) => (differences[index] ?? 0) >= 1.5 * initial && (differences[index] ?? 0) <= 0.02 && Number.isFinite(differences[index])); if (selected.length < 8) return [0, 0, selected.length];
  const xs = selected.map((index) => times[index] ?? 0); const ys = selected.map((index) => Math.log(differences[index] ?? Number.EPSILON)); const xm = xs.reduce((sum, value) => sum + value, 0) / xs.length; const ym = ys.reduce((sum, value) => sum + value, 0) / ys.length; const denominator = xs.reduce((sum, value) => sum + (value - xm) ** 2, 0); const slope = xs.reduce((sum, value, index) => sum + (value - xm) * ((ys[index] ?? 0) - ym), 0) / Math.max(denominator, Number.EPSILON); const intercept = ym - slope * xm; const residual = xs.reduce((sum, value, index) => sum + ((ys[index] ?? 0) - (intercept + slope * value)) ** 2, 0); const total = ys.reduce((sum, value) => sum + (value - ym) ** 2, 0); return [slope, 1 - residual / Math.max(total, Number.EPSILON), selected.length];
}

export function runChaosSensitivity(base: Scenario, selected: WakeCase, duration = 12, epsilon = 1e-4): ExperimentEnvelope {
  const scenario = chaoticScenario(base, selected, duration); const foil = new NacaFoil(scenario.foil); const reference = createSolver("stable-fluids"); const perturbed = createSolver("stable-fluids"); reference.initialize(scenario, scenario.seed); perturbed.initialize(scenario, scenario.seed); const state = perturbed.exportState(); const velocity = state.velocity.slice(); const {nx, ny, dx, dy} = dimensions(scenario.domain); const {x: bx, y: by} = bounds2d(scenario.domain); const stream = new Float64Array(nx * ny); const wake = new Uint8Array(nx * ny); let maximumPerturbation = 0; const px = new Float64Array(nx * ny); const py = new Float64Array(nx * ny);
  for (let y = 0; y < ny; y += 1) for (let x = 0; x < nx; x += 1) { const cell = y * nx + x; const cx = bx[0] + (x + 0.5) * dx; const cy = by[0] + (y + 0.5) * dy; const solid = foil.signedDistance(cx, cy, selected.angleDegrees) <= 0; stream[cell] = Math.exp(-(((cx - 0.2) / 0.8) ** 2) - (((cy - 0.25) / 0.5) ** 2)) * Math.sin(2 * Math.PI * (cx - bx[0]) / 1.3) * Math.sin(2 * Math.PI * (cy - by[0]) / 0.9); wake[cell] = cx > (scenario.foil.pivot[0] ?? 0) && !solid ? 1 : 0; }
  for (let y = 0; y < ny; y += 1) for (let x = 0; x < nx; x += 1) { const cell = y * nx + x; const cx0 = Math.max(0, x - 1); const cx1 = Math.min(nx - 1, x + 1); const cy0 = Math.max(0, y - 1); const cy1 = Math.min(ny - 1, y + 1); px[cell] = ((stream[cy1 * nx + x] ?? 0) - (stream[cy0 * nx + x] ?? 0)) / (Math.max(1, cy1 - cy0) * dy); py[cell] = -((stream[y * nx + cx1] ?? 0) - (stream[y * nx + cx0] ?? 0)) / (Math.max(1, cx1 - cx0) * dx); const cx = bx[0] + (x + 0.5) * dx; const cy = by[0] + (y + 0.5) * dy; if (foil.signedDistance(cx, cy, selected.angleDegrees) <= 0) { px[cell] = 0; py[cell] = 0; } maximumPerturbation = Math.max(maximumPerturbation, Math.hypot(px[cell] ?? 0, py[cell] ?? 0)); }
  for (let cell = 0; cell < nx * ny; cell += 1) { velocity[2 * cell] = (velocity[2 * cell] ?? 0) + epsilon * (px[cell] ?? 0) / Math.max(maximumPerturbation, Number.EPSILON); velocity[2 * cell + 1] = (velocity[2 * cell + 1] ?? 0) + epsilon * (py[cell] ?? 0) / Math.max(maximumPerturbation, Number.EPSILON); }
  const initialControl = controlAt(scenario, 0); const outcome = perturbed.importState({...state, velocity, sourceSolver: "deterministic-perturbation"}, initialControl); if (outcome.status === "rejected") throw new Error(`deterministic perturbation rejected: ${outcome.reason}`); const initial = wakeDifference(perturbed.exportState().velocity, reference.exportState().velocity, wake); const times: number[] = []; const differences: number[] = []; let simulated = 0; const started = performance.now();
  while (simulated < duration - 1e-12) { const dt = Math.min(scenario.outputDt, duration - simulated); simulated += dt; const control = controlAt(scenario, simulated); reference.advance(control, dt); perturbed.advance(control, dt); differences.push(wakeDifference(perturbed.exportState().velocity, reference.exportState().velocity, wake)); times.push(simulated); }
  const maximum = Math.max(...differences); const fit = exponentialFit(times, differences, initial); return {schema_version: 1, contract_id: "foilbench-phase2-v1", experiment: "chaotic-wake-sensitivity", language: "typescript", solver: "stable-fluids", scenario: scenario.id, parameters: {reynolds: selected.reynolds, angle_degrees: selected.angleDegrees, resolution: selected.resolution, duration, epsilon}, metrics: {initial_wake_rms_difference: initial, final_wake_rms_difference: differences.at(-1) ?? initial, maximum_wake_rms_difference: maximum, amplification: maximum / Math.max(initial, Number.EPSILON), finite_time_exponent: fit[0], exponential_fit_r_squared: fit[1], exponential_fit_samples: fit[2]}, series: {times, wake_rms_differences: differences}, wall_seconds: (performance.now() - started) / 1000};
}
