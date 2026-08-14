import type {Scenario} from "./contracts.js";

export interface WakeSpectrum {
  readonly sampleCount: number;
  readonly frequencyResolution: number;
  readonly transverseRms: number;
  readonly dominantFrequency: number;
  readonly strouhalNumber: number;
  readonly dominantPowerFraction: number;
}

export function analyzeWakeProbe(samples: readonly number[], sampleDt: number, chord: number, freestreamSpeed: number): WakeSpectrum {
  if (samples.length < 8) throw new RangeError("wake spectrum requires at least eight samples");
  if (!(sampleDt > 0) || !(chord > 0) || !(freestreamSpeed > 0)) throw new RangeError("wake spectrum scales must be positive");
  if (!samples.every(Number.isFinite)) throw new RangeError("wake probe samples must be finite");
  const count = samples.length;
  const mean = samples.reduce((sum, value) => sum + value, 0) / count;
  let squareSum = 0;
  const windowed = new Float64Array(count);
  for (let index = 0; index < count; index += 1) {
    const centered = (samples[index] ?? 0) - mean;
    squareSum += centered * centered;
    windowed[index] = centered * 0.5 * (1 - Math.cos(2 * Math.PI * index / (count - 1)));
  }
  const transverseRms = Math.sqrt(squareSum / count);
  let totalPower = 0;
  let dominantPower = 0;
  let dominantIndex = 0;
  for (let frequencyIndex = 1; frequencyIndex <= Math.floor(count / 2); frequencyIndex += 1) {
    let real = 0; let imaginary = 0;
    for (let sampleIndex = 0; sampleIndex < count; sampleIndex += 1) {
      const phase = 2 * Math.PI * frequencyIndex * sampleIndex / count;
      real += (windowed[sampleIndex] ?? 0) * Math.cos(phase);
      imaginary -= (windowed[sampleIndex] ?? 0) * Math.sin(phase);
    }
    const power = real * real + imaginary * imaginary;
    totalPower += power;
    if (power > dominantPower) { dominantPower = power; dominantIndex = frequencyIndex; }
  }
  const frequencyResolution = 1 / (count * sampleDt);
  const dominantFrequency = totalPower <= Number.MIN_VALUE ? 0 : dominantIndex * frequencyResolution;
  return {
    sampleCount: count,
    frequencyResolution,
    transverseRms,
    dominantFrequency,
    strouhalNumber: dominantFrequency * chord / freestreamSpeed,
    dominantPowerFraction: totalPower <= Number.MIN_VALUE ? 0 : dominantPower / totalPower,
  };
}

export function recoveryWindow(scenario: Pick<Scenario, "controls" | "duration">, duration = scenario.duration): readonly [number, number] | null {
  const first = scenario.controls[0]; const last = scenario.controls.at(-1);
  if (first === undefined || last === undefined || Math.abs(first.angleDegrees - last.angleDegrees) > 1e-9) return null;
  const changed = scenario.controls.map((control, index) => Math.abs(control.angleDegrees - first.angleDegrees) > 1e-9 ? index : -1).filter((index) => index >= 0);
  const firstChanged = changed[0]; const lastChanged = changed.at(-1);
  if (firstChanged === undefined || lastChanged === undefined || firstChanged === 0 || lastChanged + 1 >= scenario.controls.length) return null;
  const baselineEnd = scenario.controls[firstChanged - 1]?.time; const recoveryStart = scenario.controls[lastChanged + 1]?.time;
  if (baselineEnd === undefined || recoveryStart === undefined || baselineEnd >= recoveryStart || recoveryStart >= duration) return null;
  return [baselineEnd, recoveryStart];
}

export function recoveryDiagnostics(
  values: Readonly<Record<string, number>>,
): readonly [wakeWidth: number, recirculationArea: number] {
  const wakeWidth = values["wake_width"];
  const recirculationArea = values["recirculation_area"];
  if (wakeWidth === undefined || !Number.isFinite(wakeWidth) || wakeWidth < 0) {
    throw new TypeError("benchmark diagnostics omit valid wake_width");
  }
  if (recirculationArea === undefined || !Number.isFinite(recirculationArea) || recirculationArea < 0) {
    throw new TypeError("benchmark diagnostics omit valid recirculation_area");
  }
  return [wakeWidth, recirculationArea];
}

export function alignedRecoveryTimestep(
  elapsed: number,
  requestedDt: number,
  events: readonly [number, number] | null,
  tolerance = 1e-12,
): number {
  let selected = requestedDt;
  if (events !== null) for (const eventTime of events) {
    const untilEvent = eventTime - elapsed;
    if (untilEvent > tolerance && untilEvent < selected) selected = untilEvent;
  }
  return selected;
}
