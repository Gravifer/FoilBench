import type {Scenario, SolverId} from "../core/contracts.js";

export type ViewerCommand =
  | {readonly kind: "initialize"; readonly sequence: number; readonly scenario: Scenario; readonly solverId: SolverId}
  | {readonly kind: "pause" | "reset" | "release-angle" | "toggle-vorticity" | "toggle-crop" | "toggle-tracers"; readonly sequence: number}
  | {readonly kind: "switch"; readonly sequence: number; readonly solverId: SolverId}
  | {readonly kind: "set-reynolds"; readonly sequence: number; readonly reynolds: number}
  | {readonly kind: "set-angle"; readonly sequence: number; readonly angleDegrees: number; readonly timestamp: number}
  | {readonly kind: "adjust-tuning"; readonly sequence: number; readonly amount: -1 | 1}
  | {readonly kind: "release-buffers"; readonly sequence: number; readonly buffers: readonly ArrayBuffer[]};
type WithoutSequence<T> = T extends unknown ? Omit<T, "sequence"> : never;
export type ViewerCommandInput = WithoutSequence<ViewerCommand>;

export interface ViewerSnapshot {
  readonly kind: "snapshot"; readonly revision: number; readonly appliedCommand: number;
  readonly solverId: SolverId; readonly time: number; readonly angleDegrees: number; readonly reynolds: number;
  readonly paused: boolean; readonly vorticityVisible: boolean; readonly cropEnabled: boolean; readonly tracerMode: "display" | "flow";
  readonly stepRate: number | null; readonly simulatedPerWall: number | null; readonly substeps: number;
  readonly maxSpeed: number; readonly diagnostics: Readonly<Record<string, number>>; readonly status: string;
  readonly recoveryEpoch: number; readonly poseOnly: boolean; readonly scheduleActive: boolean;
  readonly solverTuning: string;
  readonly resolution: readonly [number, number]; readonly bounds: readonly [readonly [number, number], readonly [number, number]];
  readonly tracerPositions: Float32Array; readonly pathSegments: Float32Array; readonly vorticity: Float32Array; readonly foilOutline: Float32Array;
}
