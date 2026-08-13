import Ajv2020 from "ajv/dist/2020.js";
import type {Scenario, SolverOptions} from "./contracts.js";

interface RawScenario {
  schema_version: 1; id: string; dimension: 2 | 3;
  bounds: [number, number][]; resolution: number[]; periodic_axes: ("x" | "y" | "z")[];
  reynolds: number; freestream: number[];
  foil: {naca: string; chord: number; pivot: number[]};
  controls: {time: number; angle_degrees: number}[];
  duration: number; output_dt: number; precision: "float32" | "float64"; seed: number;
  solver_options?: Record<string, unknown>;
}

const optionNames: Record<string, keyof SolverOptions> = {
  initial_condition: "initialCondition", stable_advection: "stableAdvection",
  stable_face_advection: "stableFaceAdvection", stable_cfl: "stableCfl",
  pressure_tolerance: "pressureTolerance", pressure_max_iterations: "pressureMaxIterations",
  mac_maximum_divergence_linf: "macMaximumDivergenceLinf", mac_maximum_solid_leakage: "macMaximumSolidLeakage",
  pic_flip_blend: "picFlipBlend", pic_population_interval: "picPopulationInterval",
  pic_cfl: "picCfl", viewer_crop_cells: "viewerCropCells", viewer_crop_default: "viewerCropDefault",
};

function required<T>(value: T | undefined, label: string): T {
  if (value === undefined) throw new RangeError(`${label} is missing`);
  return value;
}

export function validateDocument(document: unknown, schema: object): void {
  const ajv = new Ajv2020({strict: true, allErrors: true});
  const validate = ajv.compile(schema);
  if (!validate(document)) throw new TypeError(ajv.errorsText(validate.errors, {separator: "; "}));
}

export function parseScenario(document: unknown, schema: object): Scenario {
  validateDocument(document, schema);
  const raw = document as RawScenario;
  for (let index = 1; index < raw.controls.length; index += 1) {
    if (required(raw.controls[index], "control").time < required(raw.controls[index - 1], "control").time) throw new RangeError("control times must be nondecreasing");
  }
  const options: Record<string, unknown> = {};
  for (const [rawName, value] of Object.entries(raw.solver_options ?? {})) {
    options[required(optionNames[rawName], "solver option name")] = value;
  }
  return {
    schemaVersion: 1, id: raw.id,
    domain: {dimension: raw.dimension, bounds: raw.bounds, resolution: raw.resolution, periodicAxes: raw.periodic_axes},
    reynolds: raw.reynolds, freestream: raw.freestream,
    foil: {naca: raw.foil.naca, chord: raw.foil.chord, pivot: raw.foil.pivot},
    controls: raw.controls.map((value) => ({time: value.time, angleDegrees: value.angle_degrees})),
    duration: raw.duration, outputDt: raw.output_dt, precision: raw.precision, seed: raw.seed,
    solverOptions: options,
  };
}

export function controlAt(scenario: Scenario, time: number) {
  const controls = scenario.controls;
  const first = required(controls[0], "first control");
  if (controls.length === 1 || time <= first.time) return {time, angleDegrees: first.angleDegrees, angularVelocityDegrees: 0};
  const last = required(controls.at(-1), "last control");
  if (time >= last.time) return {time, angleDegrees: last.angleDegrees, angularVelocityDegrees: 0};
  for (let index = 0; index + 1 < controls.length; index += 1) {
    const left = required(controls[index], "left control");
    const right = required(controls[index + 1], "right control");
    if (left.time <= time && time <= right.time) {
      const duration = right.time - left.time;
      if (duration <= 0) return {time, angleDegrees: right.angleDegrees, angularVelocityDegrees: 0};
      const linear = (time - left.time) / duration;
      const smooth = linear * linear * (3 - 2 * linear);
      const delta = right.angleDegrees - left.angleDegrees;
      return {time, angleDegrees: left.angleDegrees + smooth * delta, angularVelocityDegrees: 6 * linear * (1 - linear) * delta / duration};
    }
  }
  return {time, angleDegrees: last.angleDegrees, angularVelocityDegrees: 0};
}
