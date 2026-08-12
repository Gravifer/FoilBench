import {NumericalFailure, type CanonicalFlowState, type ControlState, type ImportOutcome, type Scenario} from "./contracts.js";
import {rejectedImport} from "./outcomes.js";

const rejected = (reason: Exclude<ImportOutcome["reason"], "none">): ImportOutcome => rejectedImport(reason, "canonical-import");

function sameNumbers(left: readonly number[], right: readonly number[], tolerance = 0): boolean {
  return left.length === right.length && left.every((value, index) => Math.abs(value - (right[index] ?? Number.NaN)) <= tolerance);
}

function sameAxes(left: readonly string[], right: readonly string[]): boolean {
  if (left.length !== right.length) return false;
  const selected = new Set(left);
  return selected.size === left.length && right.every((axis) => selected.has(axis));
}

export function validateCanonicalState(
  state: CanonicalFlowState,
  scenario: Scenario,
  control: ControlState,
): ImportOutcome | null {
  const runtimeSchemaVersion: unknown = Reflect.get(state, "schemaVersion");
  const expectedVelocity = scenario.domain.resolution.reduce((total, size) => total * size, 2);
  const expectedDensity = expectedVelocity / 2;
  const tolerance = (scenario.precision === "float32" ? 1e-6 : 1e-12) * Math.max(
    1,
    ...state.bounds.flatMap((bound) => bound.map(Math.abs)),
    ...scenario.domain.bounds.flatMap((bound) => bound.map(Math.abs)),
  );
  const correctArrayType = scenario.precision === "float32"
    ? state.velocity instanceof Float32Array && (state.density === null || state.density instanceof Float32Array)
    : state.velocity instanceof Float64Array && (state.density === null || state.density instanceof Float64Array);
  if (
    runtimeSchemaVersion !== 1
    || state.dimension !== scenario.domain.dimension
    || !sameNumbers(state.resolution, scenario.domain.resolution)
    || state.bounds.length !== scenario.domain.bounds.length
    || !state.bounds.every((bounds, index) => sameNumbers(bounds, scenario.domain.bounds[index] ?? [], tolerance))
    || !sameAxes(state.periodicAxes, scenario.domain.periodicAxes)
    || state.precision !== scenario.precision
    || !correctArrayType
    || state.velocity.length !== expectedVelocity
    || (state.density !== null && state.density.length !== expectedDensity)
  ) return rejected("incompatible_domain");
  if (
    !Number.isFinite(state.time)
    || state.time < 0
    || !Number.isFinite(state.angleDegrees)
    || !Number.isFinite(state.angularVelocityDegrees)
    || !state.velocity.every(Number.isFinite)
    || (state.density !== null && !state.density.every(Number.isFinite))
  ) return rejected("nonfinite_state");
  if (
    !Number.isFinite(control.time)
    || !Number.isFinite(control.angleDegrees)
    || !Number.isFinite(control.angularVelocityDegrees)
  ) return rejected("time_contract_failure");
  const controlTolerance = (scenario.precision === "float32" ? 1e-6 : 1e-12) * Math.max(
    1,
    Math.abs(state.time),
    Math.abs(control.time),
    Math.abs(state.angleDegrees),
    Math.abs(control.angleDegrees),
    Math.abs(state.angularVelocityDegrees),
    Math.abs(control.angularVelocityDegrees),
  );
  if (
    Math.abs(state.time - control.time) > controlTolerance
    || Math.abs(state.angleDegrees - control.angleDegrees) > controlTolerance
    || Math.abs(state.angularVelocityDegrees - control.angularVelocityDegrees) > controlTolerance
  ) return rejected("time_contract_failure");
  return null;
}

export function requireFiniteControl(control: ControlState): void {
  if (!Number.isFinite(control.time) || !Number.isFinite(control.angleDegrees) || !Number.isFinite(control.angularVelocityDegrees)) {
    throw new NumericalFailure("time_contract_failure", "control state must be finite", "time-mapping");
  }
}
