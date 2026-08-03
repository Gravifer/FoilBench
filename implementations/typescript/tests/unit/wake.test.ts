import {describe, expect, it} from "vitest";
import {analyzeWakeProbe, recoveryWindow} from "../../src/core/wake.js";

describe("wake analysis", () => {
  it("locates a resolved sinusoidal shedding frequency", () => {
    const samples = Array.from({length: 64}, (_, index) => 2 + 0.4 * Math.sin(2 * Math.PI * 5 * index / 64));
    const result = analyzeWakeProbe(samples, 0.1, 1, 2);
    expect(result.transverseRms).toBeCloseTo(0.4 / Math.sqrt(2), 10);
    expect(result.dominantFrequency).toBeCloseTo(5 / 6.4, 10);
    expect(result.strouhalNumber).toBeCloseTo(5 / 12.8, 10);
    expect(result.dominantPowerFraction).toBeGreaterThan(0.6);
  });

  it("recognizes a completed angle excursion inside the measured duration", () => {
    const scenario = {duration: 10, controls: [{time: 0, angleDegrees: 4}, {time: 2, angleDegrees: 4}, {time: 4, angleDegrees: 25}, {time: 6, angleDegrees: 25}, {time: 8, angleDegrees: 4}]};
    expect(recoveryWindow(scenario)).toEqual([2, 8]);
    expect(recoveryWindow(scenario, 7)).toBeNull();
  });
});
