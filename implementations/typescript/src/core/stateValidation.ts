import type {CanonicalFlowState, ControlState, ImportOutcome, Scenario} from "./contracts.js";
import {rejectedImport} from "./outcomes.js";

const rejected = (reason: Exclude<ImportOutcome["reason"], "none">): ImportOutcome => rejectedImport(reason, "canonical-import");

function sameNumbers(left: readonly number[], right: readonly number[]): boolean {
  return left.length === right.length && left.every((value, index) => value === right[index]);
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
  const expectedVelocity = scenario.domain.resolution.reduce((total, size) => total * size, 2);
  const expectedDensity = expectedVelocity / 2;
  if (
    state.dimension !== scenario.domain.dimension
    || !sameNumbers(state.resolution, scenario.domain.resolution)
    || state.bounds.length !== scenario.domain.bounds.length
    || !state.bounds.every((bounds, index) => sameNumbers(bounds, scenario.domain.bounds[index] ?? []))
    || !sameAxes(state.periodicAxes, scenario.domain.periodicAxes)
    || state.precision !== scenario.precision
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
  const tolerance = (scenario.precision === "float32" ? 1e-6 : 1e-12) * Math.max(
    1,
    Math.abs(state.time),
    Math.abs(control.time),
    Math.abs(state.angleDegrees),
    Math.abs(control.angleDegrees),
    Math.abs(state.angularVelocityDegrees),
    Math.abs(control.angularVelocityDegrees),
  );
  if (
    Math.abs(state.time - control.time) > tolerance
    || Math.abs(state.angleDegrees - control.angleDegrees) > tolerance
    || Math.abs(state.angularVelocityDegrees - control.angularVelocityDegrees) > tolerance
  ) return rejected("time_contract_failure");
  return null;
}
