import type {Scenario, SolverId} from "../core/contracts.js";
import {NumericalFailure} from "../core/contracts.js";
import {createSolver} from "../solvers/factory.js";

export interface DragCandidate {
  readonly id: string;
  readonly tip_speed_cap: number;
  readonly smoothing_window_seconds: number;
}

export interface DragTrace {
  readonly id: string;
  readonly samples: readonly (readonly [number, number])[];
}

export interface DragRun {
  readonly candidate: string;
  readonly solver: SolverId;
  readonly trace: string;
  readonly tip_speed_cap: number;
  readonly smoothing_window_seconds: number;
  readonly max_measured_tip_speed_ratio: number;
  readonly max_solver_tip_speed_ratio: number;
  readonly successful_steps: number;
  readonly requested_steps: number;
  readonly failure_reason: string | null;
  readonly maximum_flow_speed: number;
  readonly wall_seconds: number;
}

export function runDragCalibration(
  base: Scenario,
  resolution: readonly [number, number],
  solverId: SolverId,
  candidate: DragCandidate,
  trace: DragTrace,
): DragRun {
  const scenario: Scenario = {
    ...base,
    domain: {...base.domain, resolution},
    controls: [
      {time: 0, angleDegrees: 0},
      {time: base.duration, angleDegrees: 0},
    ],
  };
  const solver = createSolver(solverId);
  solver.initialize(scenario, scenario.seed);
  const reference = Math.max(Math.hypot(...scenario.freestream), 1e-6);
  const recent: [number, number][] = [];
  let maximumMeasured = 0;
  let maximumSolver = 0;
  let maximumFlow = 0;
  let successful = 0;
  let failure: string | null = null;
  let physicalTime = 0;
  const started = performance.now();
  const samples = [...trace.samples, [(trace.samples.at(-1)?.[0] ?? 0) + 0.02, trace.samples.at(-1)?.[1] ?? 0] as const];
  for (let sampleIndex = 0; sampleIndex < samples.length; sampleIndex += 1) {
    const sample = samples[sampleIndex];
    if (sample === undefined) continue;
    const timestamp = sample[0];
    const angle = Math.max(-30, Math.min(30, sample[1]));
    let measuredDegrees = 0;
    if (sampleIndex < trace.samples.length) {
      recent.push([timestamp, angle]);
      const cutoff = timestamp - candidate.smoothing_window_seconds;
      while (recent.length > 2 && (recent[1]?.[0] ?? timestamp) < cutoff) recent.shift();
      const first = recent[0];
      if (first !== undefined && timestamp > first[0]) measuredDegrees = (angle - first[1]) / (timestamp - first[0]);
    }
    const measuredRatio = (Math.abs(measuredDegrees) * Math.PI / 180) * scenario.foil.chord / reference;
    const solverRatio = Math.min(measuredRatio, candidate.tip_speed_cap);
    maximumMeasured = Math.max(maximumMeasured, measuredRatio);
    maximumSolver = Math.max(maximumSolver, solverRatio);
    const omega = measuredDegrees === 0 ? 0 : Math.sign(measuredDegrees) * solverRatio * reference / scenario.foil.chord * 180 / Math.PI;
    physicalTime += scenario.outputDt;
    try {
      const report = solver.advance({time: physicalTime, angleDegrees: angle, angularVelocityDegrees: omega}, scenario.outputDt);
      maximumFlow = Math.max(maximumFlow, report.maxSpeed);
      successful += 1;
    } catch (error) {
      failure = error instanceof NumericalFailure ? error.reason : error instanceof Error ? error.name : "unknown";
      break;
    }
  }
  return {
    candidate: candidate.id,
    solver: solverId,
    trace: trace.id,
    tip_speed_cap: candidate.tip_speed_cap,
    smoothing_window_seconds: candidate.smoothing_window_seconds,
    max_measured_tip_speed_ratio: maximumMeasured,
    max_solver_tip_speed_ratio: maximumSolver,
    successful_steps: successful,
    requested_steps: trace.samples.length + 1,
    failure_reason: failure,
    maximum_flow_speed: maximumFlow,
    wall_seconds: (performance.now() - started) / 1000,
  };
}
