export type Precision = "float32" | "float64";
export type SolverId = "stable-fluids" | "lbm-d2q9" | "pic-flip";
export type FloatArray = Float32Array | Float64Array;

export interface DomainSpec {
  readonly dimension: 2 | 3;
  readonly bounds: readonly (readonly [number, number])[];
  readonly resolution: readonly number[];
  readonly periodicAxes: readonly ("x" | "y" | "z")[];
}

export interface FoilSpec {
  readonly naca: string;
  readonly chord: number;
  readonly pivot: readonly number[];
}

export interface ControlKeyframe {readonly time: number; readonly angleDegrees: number}
export interface ControlState {readonly time: number; readonly angleDegrees: number; readonly angularVelocityDegrees: number}

export interface SolverOptions {
  readonly initialCondition?: "freestream" | "taylor-green" | "poiseuille";
  readonly stableAdvection?: "maccormack" | "semi-lagrangian" | "skew-rk2";
  readonly stableFaceAdvection?: boolean;
  readonly stableCfl?: number;
  readonly pressureTolerance?: number;
  readonly pressureMaxIterations?: number;
  readonly picFlipBlend?: number;
  readonly picPopulationInterval?: number;
  readonly picCfl?: number;
  readonly viewerCropCells?: number;
  readonly viewerCropDefault?: boolean;
}

export interface Scenario {
  readonly schemaVersion: 1;
  readonly id: string;
  readonly domain: DomainSpec;
  readonly reynolds: number;
  readonly freestream: readonly number[];
  readonly foil: FoilSpec;
  readonly controls: readonly ControlKeyframe[];
  readonly duration: number;
  readonly outputDt: number;
  readonly precision: Precision;
  readonly seed: number;
  readonly solverOptions: SolverOptions;
}

export interface SolverInfo {
  readonly id: SolverId;
  readonly displayName: string;
  readonly dimensions: readonly number[];
  readonly supportsMovingBoundary: boolean;
  readonly acceleration: string;
}

export interface StepReport {
  readonly requestedDt: number;
  readonly advancedDt: number;
  readonly substeps: number;
  readonly maxSpeed: number;
  readonly warnings: readonly string[];
}

export interface Diagnostics {readonly values: Readonly<Record<string, number>>; readonly warnings: readonly string[]}
export type InteractiveTuningValue = string | number;
export interface InteractiveTuning {
  readonly id: string;
  readonly label: string;
  readonly value: InteractiveTuningValue;
  readonly canDecrease: boolean;
  readonly canIncrease: boolean;
}
export type ImportReason = "none" | "excessive_velocity" | "nonfinite_state" | "incompatible_geometry" | "incompatible_domain" | "projection_failure" | "invalid_density" | "unsupported_conversion";
export interface ImportOutcome {readonly status: "accepted" | "rejected"; readonly reason: ImportReason; readonly discardedState: readonly string[]; readonly warnings: readonly string[]}

export interface CanonicalFlowState {
  readonly schemaVersion: 1;
  readonly dimension: 2 | 3;
  readonly bounds: readonly (readonly [number, number])[];
  readonly resolution: readonly number[];
  readonly periodicAxes: readonly ("x" | "y" | "z")[];
  readonly time: number;
  readonly precision: Precision;
  readonly angleDegrees: number;
  readonly angularVelocityDegrees: number;
  readonly sourceLanguage: string;
  readonly sourceSolver: string;
  readonly velocity: FloatArray;
  readonly density: FloatArray | null;
}

export interface FlowSolver {
  readonly info: SolverInfo;
  readonly reynolds: number;
  initialize(scenario: Scenario, seed: number): void;
  setReynolds(reynolds: number): void;
  advance(control: ControlState, targetDt: number): StepReport;
  sampleVelocity(points: FloatArray): FloatArray;
  exportState(): CanonicalFlowState;
  importState(state: CanonicalFlowState, control: ControlState): ImportOutcome;
  diagnostics(): Diagnostics;
  interactiveTuning?(): InteractiveTuning;
  adjustInteractiveTuning?(direction: -1 | 1): InteractiveTuning;
  applyInteractiveTuning?(value: InteractiveTuningValue): InteractiveTuning;
}

export class NumericalFailure extends Error {
  public constructor(public readonly reason: Exclude<ImportReason, "none" | "incompatible_geometry" | "incompatible_domain" | "unsupported_conversion">, detail: string) {
    super(detail);
    this.name = "NumericalFailure";
  }
}
