export type Precision = "float32" | "float64";
export const SOLVER_IDS = ["stable-fluids", "lbm-d2q9", "pic-flip"] as const;
export type SolverId = typeof SOLVER_IDS[number];

export function isSolverId(value: string): value is SolverId {
  return (SOLVER_IDS as readonly string[]).includes(value);
}
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
export interface RestartState {readonly time: number; readonly angleDegrees: number; readonly reynolds: number}

export interface SolverOptions {
  readonly initialCondition?: "freestream" | "taylor-green" | "poiseuille";
  readonly stableAdvection?: "maccormack" | "semi-lagrangian" | "skew-rk2";
  readonly stableFaceAdvection?: boolean;
  readonly stableCfl?: number;
  readonly pressureTolerance?: number;
  readonly pressureMaxIterations?: number;
  readonly macMaximumDivergenceLinf?: number;
  readonly macMaximumSolidLeakage?: number;
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
  readonly supportedPrecisions: readonly Precision[];
  readonly acceleration: string;
}

export type FailureStage = "canonical-import" | "advection" | "viscosity" | "projection" | "boundary" | "collision" | "streaming" | "particle-transfer" | "particle-advection" | "population-maintenance" | "time-mapping" | "postcondition";
export type FailureEvidenceValue = string | number | boolean;
export type FailureEvidence = Readonly<Record<string, FailureEvidenceValue>>;

export interface StepReport {
  readonly requestedDt: number;
  readonly advancedDt: number;
  readonly substeps: number;
  readonly maxSpeed: number;
  readonly stateRevision: number;
  readonly evidence: FailureEvidence;
  readonly warnings: readonly string[];
}

export interface Diagnostics {readonly stateRevision: number; readonly values: Readonly<Record<string, number>>; readonly warnings: readonly string[]}
export type InteractiveTuningValue = string | number;
export interface InteractiveTuning {
  readonly id: string;
  readonly label: string;
  readonly value: InteractiveTuningValue;
  readonly canDecrease: boolean;
  readonly canIncrease: boolean;
}
export type FailureReason = "excessive_velocity" | "stability_limit" | "nonfinite_state" | "convergence_failure" | "projection_failure" | "invalid_density" | "invalid_population" | "invalid_relaxation" | "transfer_failure" | "postcondition_failure" | "time_contract_failure" | "incompatible_geometry" | "incompatible_domain" | "unsupported_conversion";
export type ImportReason = "none" | FailureReason;
export interface ImportOutcome {readonly status: "accepted" | "rejected"; readonly reason: ImportReason; readonly stage: FailureStage | null; readonly evidence: FailureEvidence; readonly discardedState: readonly string[]; readonly warnings: readonly string[]}
export interface ReynoldsOutcome {readonly requested: number; readonly effective: number; readonly warnings: readonly string[]}

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
  readonly stateRevision: number;
  initialize(scenario: Scenario, seed: number): void;
  restart(scenario: Scenario, seed: number, start: RestartState): void;
  setReynolds(reynolds: number): ReynoldsOutcome;
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
  public constructor(
    public readonly reason: Exclude<FailureReason, "incompatible_geometry" | "incompatible_domain" | "unsupported_conversion">,
    detail: string,
    public readonly stage: FailureStage = "postcondition",
    public readonly evidence: FailureEvidence = {},
  ) {
    super(detail);
    this.name = "NumericalFailure";
  }
}
