/// <reference lib="webworker" />
import type {FloatArray, FlowSolver, StepReport} from "../core/contracts.js";
import {NumericalFailure} from "../core/contracts.js";
import {dimensions} from "../core/grid.js";
import {controlAt} from "../core/scenario.js";
import {alignedRecoveryTimestep, analyzeWakeProbe, recoveryDiagnostics, recoveryWindow} from "../core/wake.js";
import {createSolver} from "../solvers/factory.js";
import {loadRustWasmSolverFactory} from "../wasm/rustWasmSolver.js";
import type {BrowserRunRequest, BrowserRunResult} from "./types.js";

const RECOVERY_OBSERVATION_LIMIT = 4;
const RECOVERY_TIME_TOLERANCE = 1e-9;
const RECOVERY_EVENT_TOLERANCE = 1e-12;

postMessage({kind: "ready"});

self.onmessage = (event: MessageEvent<BrowserRunRequest>): void => { void run(event.data); };

async function run(request: BrowserRunRequest): Promise<void> {
  const {scenario, solverId, duration} = request;
  const warnings: string[] = [];
  let initializationSeconds = 0; let coldStepSeconds = 0; const stepSeconds: number[] = [];
  let elapsed = 0; let substeps = 0; let diagnostics: Readonly<Record<string, number>>; let success = true; let snapshot: BrowserRunResult["snapshot"] = null; let solver: FlowSolver | null = null; let lastStep: StepReport | null = null; let diagnosticStateRevision: number | null; let failure: BrowserRunResult["failure"] = null;
  try {
    const factory = request.backend === "rust-wasm" ? await loadRustWasmSolverFactory() : createSolver;
    const cold = factory(solverId); let started = performance.now(); cold.initialize(scenario, scenario.seed); initializationSeconds = (performance.now() - started) / 1000;
    const coldDt = Math.min(scenario.outputDt, duration); started = performance.now(); cold.advance(controlAt(scenario, coldDt), coldDt); coldStepSeconds = (performance.now() - started) / 1000;
    for (let step = 2; step <= 20; step += 1) cold.advance(controlAt(scenario, step * coldDt), coldDt);
    solver = factory(solverId); solver.initialize(scenario, scenario.seed);
    const wakeProbe: number[] = []; const recovery = recoveryWindow(scenario, duration); let recoveryBaseline: readonly [number, number] | null = null; let recoveryElapsed: number | null = null;
    const {dx, dy} = dimensions(scenario.domain); const xMaximum = scenario.domain.bounds[0]?.[1] ?? 0; const probe = (scenario.precision === "float32" ? new Float32Array(2) : new Float64Array(2)) as FloatArray;
    probe[0] = Math.min((scenario.foil.pivot[0] ?? 0) + 1.5 * scenario.foil.chord, xMaximum - 0.5 * dx); probe[1] = scenario.foil.pivot[1] ?? 0;
    while (elapsed < duration - 1e-12) {
      if (recovery !== null && recoveryBaseline === null && elapsed >= recovery[0] - RECOVERY_EVENT_TOLERANCE) {
        const baseline = solver.diagnostics().values;
        recoveryBaseline = recoveryDiagnostics(baseline);
      }
      let dt = Math.min(scenario.outputDt, duration - elapsed);
      dt = alignedRecoveryTimestep(elapsed, dt, recovery, RECOVERY_EVENT_TOLERANCE);
      started = performance.now(); const report = solver.advance(controlAt(scenario, elapsed + dt), dt); lastStep = report; stepSeconds.push((performance.now() - started) / 1000); elapsed += report.advancedDt; substeps += report.substeps; warnings.push(...report.warnings);
      if (elapsed >= 0.5 * duration) wakeProbe.push(solver.sampleVelocity(probe)[1] ?? 0);
      if (recovery !== null) {
        const [baselineEnd, recoveryStart] = recovery; const crossedBaseline = recoveryBaseline === null && elapsed >= baselineEnd - RECOVERY_EVENT_TOLERANCE; const observingRecovery = recoveryBaseline !== null && recoveryElapsed === null && elapsed >= recoveryStart - RECOVERY_EVENT_TOLERANCE;
        if (crossedBaseline || observingRecovery) { const transient = solver.diagnostics().values; const [wake, recirculation] = recoveryDiagnostics(transient); if (crossedBaseline) recoveryBaseline = [wake, recirculation]; else if (recoveryBaseline !== null && wake <= Math.max(1.25 * recoveryBaseline[0], 2 * dy) && recirculation <= Math.max(1.25 * recoveryBaseline[1], 2 * dx * dy)) recoveryElapsed = elapsed - recoveryStart; }
      }
    }
    const selected = solver.diagnostics(); if (selected.stateRevision !== solver.stateRevision) throw new Error("benchmark diagnostics describe a stale state revision"); diagnosticStateRevision = selected.stateRevision; const measured: Record<string, number> = {...selected.values}; warnings.push(...selected.warnings);
    if (wakeProbe.length >= 8) { const freestreamSpeed = Math.max(Math.hypot(...scenario.freestream), 1e-12); const wake = analyzeWakeProbe(wakeProbe, scenario.outputDt, scenario.foil.chord, freestreamSpeed); Object.assign(measured, {wake_probe_samples: wake.sampleCount, wake_frequency_resolution: wake.frequencyResolution, wake_transverse_rms: wake.transverseRms, wake_mixing_index: wake.transverseRms / freestreamSpeed, wake_dominant_frequency: wake.dominantFrequency, wake_strouhal_number: wake.strouhalNumber, wake_dominant_power_fraction: wake.dominantPowerFraction}); }
    if (recovery !== null && recoveryBaseline !== null) { const observationLimit = Math.min(RECOVERY_OBSERVATION_LIMIT, Math.max(0, duration - recovery[1])); const observed = recoveryElapsed !== null && recoveryElapsed <= observationLimit + RECOVERY_TIME_TOLERANCE; const reportedElapsed = observed && recoveryElapsed !== null ? Math.min(recoveryElapsed, observationLimit) : observationLimit; Object.assign(measured, {recovery_baseline_time: recovery[0], recovery_start_time: recovery[1], recovery_observed: Number(observed), recovery_elapsed: reportedElapsed}); if (!observed) warnings.push("wake recovery was not observed; recovery_elapsed is right-censored"); }
    diagnostics = measured;
    const state = solver.exportState(); snapshot = {precision: state.precision, bounds: state.bounds, resolution: state.resolution, periodicAxes: state.periodicAxes, time: state.time, angleDegrees: state.angleDegrees, angularVelocityDegrees: state.angularVelocityDegrees, velocity: [...state.velocity], density: state.density === null ? null : [...state.density]};
  } catch (error) { success = false; const message = error instanceof Error ? `${error.name}: ${error.message}` : "unknown benchmark failure"; warnings.push(message); failure = error instanceof NumericalFailure ? {kind: "numerical", reason: error.reason, stage: error.stage, message: error.message, evidence: error.evidence} : {kind: "unexpected", reason: null, stage: null, message, evidence: {}}; diagnostics = {}; diagnosticStateRevision = null; }
  const result: BrowserRunResult = {initializationSeconds, coldStepSeconds, stepSeconds, simulatedSeconds: elapsed, substeps, finalStateRevision: solver?.stateRevision ?? 0, diagnosticStateRevision, lastStep, diagnostics, warnings: [...new Set(warnings)].sort(), success, failure, snapshot}; postMessage({kind: "result", result});
}
