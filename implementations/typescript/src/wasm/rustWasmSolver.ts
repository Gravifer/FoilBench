import type {
  CanonicalFlowState,
  ControlState,
  Diagnostics,
  FailureReason,
  FailureStage,
  FlowSolver,
  FloatArray,
  ImportOutcome,
  InteractiveTuning,
  InteractiveTuningValue,
  RestartState,
  ReynoldsOutcome,
  Scenario,
  SolverId,
  SolverInfo,
  StepReport,
} from "../core/contracts.js";
import {NumericalFailure} from "../core/contracts.js";

interface WasmSolverBinding {
  readonly precision: string;
  readonly reynolds: number;
  readonly state_revision: bigint;
  restart(time: number, angle: number, reynolds: number, seed: number): void;
  advance_json(time: number, angle: number, angularVelocity: number, targetDt: number): string;
  set_reynolds_json(reynolds: number): string;
  tuning_json(): string;
  set_transport(mode: string): void;
  sample_velocity_f32(points: Float32Array): Float32Array;
  sample_velocity_f64(points: Float64Array): Float64Array;
  diagnostics_json(): string;
  export_state_metadata_json(): string;
  export_velocity_f32(): Float32Array;
  export_velocity_f64(): Float64Array;
  import_state_f32_json(metadata: string, velocity: Float32Array): string;
  import_state_f64_json(metadata: string, velocity: Float64Array): string;
  dispose(): void;
  free(): void;
}

interface WasmModule {
  default(input?: URL): Promise<unknown>;
  WasmSolver: new (scenarioJson: string, solverId: string, seed: number) => WasmSolverBinding;
}

const reasons = new Set<FailureReason>([
  "excessive_velocity", "stability_limit", "nonfinite_state", "convergence_failure",
  "projection_failure", "invalid_density", "invalid_population", "invalid_relaxation",
  "transfer_failure", "postcondition_failure", "time_contract_failure",
  "incompatible_geometry", "incompatible_domain", "unsupported_conversion",
]);
const stages = new Set<FailureStage>([
  "initialization", "restart", "canonical-import", "advection", "viscosity", "projection", "boundary", "collision",
  "streaming", "particle-transfer", "particle-advection", "population-maintenance",
  "time-mapping", "postcondition",
]);

function parseObject(document: string): Record<string, unknown> {
  const parsed: unknown = JSON.parse(document);
  if (parsed === null || typeof parsed !== "object" || Array.isArray(parsed)) throw new TypeError("Rust/WASM returned non-object JSON");
  return parsed as Record<string, unknown>;
}

function numberValue(object: Readonly<Record<string, unknown>>, key: string): number {
  const value = object[key];
  if (typeof value !== "number" || !Number.isFinite(value)) throw new TypeError(`Rust/WASM field ${key} is not finite`);
  return value;
}

function strings(value: unknown): string[] {
  if (!Array.isArray(value) || !value.every((child) => typeof child === "string")) throw new TypeError("Rust/WASM warning list is malformed");
  return [...value];
}

function classified(error: unknown): Error {
  const text = typeof error === "string" ? error : error instanceof Error ? error.message : String(error);
  try {
    const parsed = parseObject(text);
    const reason = parsed["reason"];
    const stage = parsed["stage"];
    const message = typeof parsed["message"] === "string" ? parsed["message"] : "Rust/WASM numerical failure";
    if (typeof reason === "string" && reasons.has(reason as FailureReason)) {
      const selectedStage = typeof stage === "string" && stages.has(stage as FailureStage) ? stage as FailureStage : "postcondition";
      const numerical = reason as FailureReason;
      if (numerical !== "incompatible_geometry" && numerical !== "incompatible_domain" && numerical !== "unsupported_conversion") return new NumericalFailure(numerical, message, selectedStage);
    }
    return new Error(message);
  } catch {
    return error instanceof Error ? error : new Error(text);
  }
}

function invoke<T>(operation: () => T): T {
  try { return operation(); } catch (error) { throw classified(error); }
}

function scenarioWire(scenario: Scenario): string {
  const options: Record<string, unknown> = {};
  const mappings: readonly (readonly [keyof Scenario["solverOptions"], string])[] = [
    ["initialCondition", "initial_condition"], ["stableAdvection", "stable_advection"],
    ["stableFaceAdvection", "stable_face_advection"], ["stableCfl", "stable_cfl"],
    ["pressureTolerance", "pressure_tolerance"], ["pressureMaxIterations", "pressure_max_iterations"],
    ["macMaximumDivergenceLinf", "mac_maximum_divergence_linf"],
    ["macMaximumSolidLeakage", "mac_maximum_solid_leakage"], ["picFlipBlend", "pic_flip_blend"],
    ["picPopulationInterval", "pic_population_interval"], ["picCfl", "pic_cfl"],
    ["viewerCropCells", "viewer_crop_cells"], ["viewerCropDefault", "viewer_crop_default"],
  ];
  for (const [source, target] of mappings) {
    const value = scenario.solverOptions[source];
    if (value !== undefined) options[target] = value;
  }
  return JSON.stringify({
    schema_version: 1, id: scenario.id, dimension: scenario.domain.dimension,
    bounds: scenario.domain.bounds, resolution: scenario.domain.resolution,
    periodic_axes: scenario.domain.periodicAxes, reynolds: scenario.reynolds,
    freestream: scenario.freestream,
    foil: {family: "naca-four-digit-v1", naca: scenario.foil.naca, chord: scenario.foil.chord, pivot: scenario.foil.pivot},
    controls: scenario.controls.map((control) => ({time: control.time, angle_degrees: control.angleDegrees})),
    duration: scenario.duration, output_dt: scenario.outputDt, precision: scenario.precision,
    seed: scenario.seed, solver_options: options,
  });
}

let modulePromise: Promise<WasmModule> | null = null;

export async function loadRustWasmSolverFactory(): Promise<(id: SolverId) => FlowSolver> {
  modulePromise ??= (async (): Promise<WasmModule> => {
    const script = new URL("/rust-wasm/foilbench_wasm.js", self.location.origin).href;
    const imported: unknown = await import(/* @vite-ignore */ script);
    const module = imported as WasmModule;
    await module.default(new URL("/rust-wasm/foilbench_wasm_bg.wasm", self.location.origin));
    return module;
  })();
  const module = await modulePromise;
  return (id): FlowSolver => new RustWasmFlowSolver(module, id);
}

export class RustWasmFlowSolver implements FlowSolver {
  public readonly info: SolverInfo;
  private binding: WasmSolverBinding | null = null;
  private scenario: Scenario | null = null;

  public constructor(private readonly module: WasmModule, private readonly solverId: SolverId) {
    this.info = {
      id: solverId, displayName: `${solverId} (Rust/WASM)`, dimensions: [2],
      supportsMovingBoundary: true, supportedPrecisions: ["float32", "float64"],
      acceleration: "Rust/WASM shared core",
    };
  }

  private selected(): WasmSolverBinding {
    if (this.binding === null) throw new Error("Rust/WASM solver is not initialized");
    return this.binding;
  }

  public get reynolds(): number { return this.selected().reynolds; }
  public get stateRevision(): number { return Number(this.selected().state_revision); }

  public initialize(scenario: Scenario, seed: number): void {
    this.binding?.dispose();
    this.binding?.free();
    this.scenario = scenario;
    this.binding = invoke(() => new this.module.WasmSolver(scenarioWire(scenario), this.solverId, seed));
  }

  public restart(scenario: Scenario, seed: number, start: RestartState): void {
    if (this.scenario !== scenario) this.initialize(scenario, seed);
    invoke(() => this.selected().restart(start.time, start.angleDegrees, start.reynolds, seed));
  }

  public setReynolds(reynolds: number): ReynoldsOutcome {
    const parsed = parseObject(invoke(() => this.selected().set_reynolds_json(reynolds)));
    return {requested: numberValue(parsed, "requested"), effective: numberValue(parsed, "effective"), warnings: strings(parsed["warnings"])};
  }

  public advance(control: ControlState, targetDt: number): StepReport {
    const parsed = parseObject(invoke(() => this.selected().advance_json(control.time, control.angleDegrees, control.angularVelocityDegrees, targetDt)));
    return {
      requestedDt: numberValue(parsed, "requestedDt"), advancedDt: numberValue(parsed, "advancedDt"),
      substeps: numberValue(parsed, "substeps"), maxSpeed: numberValue(parsed, "maxSpeed"),
      stateRevision: numberValue(parsed, "stateRevision"),
      evidence: (parsed["evidence"] ?? {}) as Readonly<Record<string, string | number | boolean>>,
      warnings: strings(parsed["warnings"]),
    };
  }

  public sampleVelocity(points: FloatArray): FloatArray {
    if (points instanceof Float32Array) return invoke(() => this.selected().sample_velocity_f32(points));
    return invoke(() => this.selected().sample_velocity_f64(points));
  }

  public exportState(): CanonicalFlowState {
    const binding = this.selected();
    const parsed = parseObject(invoke(() => binding.export_state_metadata_json()));
    const velocity = binding.precision === "float32" ? invoke(() => binding.export_velocity_f32()) : invoke(() => binding.export_velocity_f64());
    const bounds = parsed["bounds"] as readonly (readonly [number, number])[];
    const resolution = parsed["resolution"] as readonly number[];
    const periodicAxes = parsed["periodic_axes"] as readonly ("x" | "y" | "z")[];
    return {
      schemaVersion: 1, dimension: 2, bounds, resolution, periodicAxes,
      time: numberValue(parsed, "time"), precision: binding.precision as "float32" | "float64",
      angleDegrees: numberValue(parsed, "angle_degrees"),
      angularVelocityDegrees: numberValue(parsed, "angular_velocity_degrees"),
      sourceLanguage: "rust", sourceSolver: String(parsed["source_solver"]), velocity, density: null,
    };
  }

  public importState(state: CanonicalFlowState, control: ControlState): ImportOutcome {
    const scenario = this.scenario;
    if (scenario === null) throw new Error("Rust/WASM solver is not initialized");
    const metadata = JSON.stringify({
      bounds: state.bounds, resolution: state.resolution, periodic_axes: state.periodicAxes,
      time: control.time, angle_degrees: control.angleDegrees,
      angular_velocity_degrees: control.angularVelocityDegrees,
      geometry: {family: "naca-four-digit-v1", naca: scenario.foil.naca, chord: scenario.foil.chord, pivot: scenario.foil.pivot},
      producer: {implementation: state.sourceLanguage, execution_target: "native", build: null},
      source_solver: state.sourceSolver,
    });
    const document = state.velocity instanceof Float32Array
      ? invoke(() => this.selected().import_state_f32_json(metadata, state.velocity as Float32Array))
      : invoke(() => this.selected().import_state_f64_json(metadata, state.velocity as Float64Array));
    const parsed = parseObject(document);
    return {
      status: parsed["status"] as "accepted" | "rejected",
      reason: parsed["reason"] as ImportOutcome["reason"],
      stage: parsed["stage"] as FailureStage,
      evidence: (parsed["evidence"] ?? {}) as Readonly<Record<string, string | number | boolean>>,
      discardedState: strings(parsed["discardedState"]), warnings: strings(parsed["warnings"]),
    };
  }

  public diagnostics(): Diagnostics {
    const parsed = parseObject(invoke(() => this.selected().diagnostics_json()));
    return {stateRevision: numberValue(parsed, "stateRevision"), values: parsed["values"] as Readonly<Record<string, number>>, warnings: strings(parsed["warnings"])};
  }

  public interactiveTuning(): InteractiveTuning {
    return parseObject(invoke(() => this.selected().tuning_json())) as unknown as InteractiveTuning;
  }

  public adjustInteractiveTuning(direction: -1 | 1): InteractiveTuning {
    this.applyInteractiveTuning(direction < 0 ? "maccormack" : "skew-rk2");
    return this.interactiveTuning();
  }

  public applyInteractiveTuning(value: InteractiveTuningValue): InteractiveTuning {
    if (typeof value !== "string") throw new TypeError("Stable Fluids transport tuning must be a string");
    invoke(() => this.selected().set_transport(value));
    return this.interactiveTuning();
  }
}
