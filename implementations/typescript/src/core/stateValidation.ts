import {NumericalFailure, type CanonicalFlowState, type ControlState, type ImportOutcome, type Scenario} from "./contracts.js";
import {NacaFoil} from "./geometry.js";
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
    (runtimeSchemaVersion !== 1 && runtimeSchemaVersion !== 2)
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
  if (runtimeSchemaVersion === 2) {
    const geometry = state.geometry;
    if (
      geometry === undefined
      || state.producerExecutionTarget === undefined
      || geometry.naca !== scenario.foil.naca
      || Math.abs(geometry.chord - scenario.foil.chord) > tolerance
      || !sameNumbers(geometry.pivot, scenario.foil.pivot, tolerance)
    ) return rejected("incompatible_geometry");
  }
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
  if (state.dimension === 2) {
    const nx = scenario.domain.resolution[0] ?? 0; const ny = scenario.domain.resolution[1] ?? 0; const xBounds = scenario.domain.bounds[0] ?? [0, 0]; const yBounds = scenario.domain.bounds[1] ?? [0, 0]; const dx = (xBounds[1] - xBounds[0]) / nx; const dy = (yBounds[1] - yBounds[0]) / ny; const foil = new NacaFoil(scenario.foil); let nonzero = 0;
    for (let y = 0; y < ny; y += 1) for (let x = 0; x < nx; x += 1) { const px = xBounds[0] + (x + 0.5) * dx; const py = yBounds[0] + (y + 0.5) * dy; if (foil.signedDistance(px, py, control.angleDegrees) <= 0 && ((state.velocity[2 * (y * nx + x)] ?? 0) !== 0 || (state.velocity[2 * (y * nx + x) + 1] ?? 0) !== 0)) nonzero += 1; }
    if (nonzero > 0) return rejectedImport("postcondition_failure", "canonical-import", {nonzero_solid_cells: nonzero});
  }
  return null;
}

export function requireFiniteControl(control: ControlState): void {
  if (!Number.isFinite(control.time) || !Number.isFinite(control.angleDegrees) || !Number.isFinite(control.angularVelocityDegrees)) {
    throw new NumericalFailure("time_contract_failure", "control state must be finite", "time-mapping");
  }
}
