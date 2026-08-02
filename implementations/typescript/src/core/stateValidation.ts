import type {CanonicalFlowState, ImportOutcome, Scenario} from "./contracts.js";

const rejected = (reason: ImportOutcome["reason"]): ImportOutcome => ({
  status: "rejected",
  reason,
  discardedState: [],
  warnings: [],
});

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
): ImportOutcome | null {
  const expectedVelocity = scenario.domain.resolution.reduce((total, size) => total * size, 2);
  const expectedDensity = expectedVelocity / 2;
  if (
    state.dimension !== scenario.domain.dimension
    || !sameNumbers(state.resolution, scenario.domain.resolution)
    || state.bounds.length !== scenario.domain.bounds.length
    || !state.bounds.every((bounds, index) => sameNumbers(bounds, scenario.domain.bounds[index] ?? []))
    || !sameAxes(state.periodicAxes, scenario.domain.periodicAxes)
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
  return null;
}
