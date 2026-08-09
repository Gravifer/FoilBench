import type {Scenario, SolverId} from "../core/contracts.js";

export type ViewerCommand =
  | {readonly kind: "initialize"; readonly sequence: number; readonly scenario: Scenario; readonly solverId: SolverId}
  | {readonly kind: "pause" | "reset" | "release-angle" | "toggle-vorticity" | "toggle-crop" | "toggle-tracers" | "toggle-diagnostics"; readonly sequence: number}
  | {readonly kind: "shutdown"; readonly sequence: number}
  | {readonly kind: "switch"; readonly sequence: number; readonly solverId: SolverId}
  | {readonly kind: "set-reynolds"; readonly sequence: number; readonly reynolds: number}
  | {readonly kind: "set-angle"; readonly sequence: number; readonly angleDegrees: number; readonly timestamp: number}
  | {readonly kind: "adjust-tuning"; readonly sequence: number; readonly amount: -1 | 1}
  | {readonly kind: "visibility"; readonly sequence: number; readonly visible: boolean};
type WithoutSequence<T> = T extends unknown ? Omit<T, "sequence"> : never;
export type ViewerCommandInput = WithoutSequence<ViewerCommand>;
export interface SnapshotConsumed {readonly kind: "snapshot-consumed"; readonly revision: number}
export interface ShutdownAcknowledgement {readonly kind: "shutdown-ack"; readonly appliedCommand: number}
export interface ViewerStatusEvent {
  readonly kind: "status";
  readonly revision: number;
  readonly appliedCommand: number;
  readonly phase: "warming" | "running" | "paused" | "failed";
  readonly status: string;
  readonly recoveryEpoch: number;
  readonly recoveryReason: string | null;
  readonly recoveryStage: string | null;
}

export interface ViewerSnapshot {
  readonly kind: "snapshot"; readonly revision: number; readonly appliedCommand: number;
  readonly solverId: SolverId; readonly time: number; readonly angleDegrees: number; readonly reynolds: number; readonly playbackRate: number;
  readonly paused: boolean; readonly vorticityVisible: boolean; readonly cropEnabled: boolean; readonly tracerMode: "display" | "material";
  readonly stepRate: number | null; readonly simulatedPerWall: number | null; readonly substeps: number;
  readonly maxSpeed: number; readonly diagnostics: Readonly<Record<string, number>>; readonly status: string;
  readonly recoveryEpoch: number; readonly recoveryReason: string | null; readonly recoveryStage: string | null;
  readonly poseOnly: boolean; readonly motionMode: "resolved" | "pose-only"; readonly scheduleActive: boolean;
  readonly phase: "warming" | "running" | "paused" | "failed"; readonly diagnosticMode: "cadenced" | "every-step";
  readonly solverTuning: string;
  readonly resolution: readonly [number, number]; readonly bounds: readonly [readonly [number, number], readonly [number, number]];
  readonly tracerPositions: Float32Array; readonly pathSegments: Float32Array; readonly vorticity: Float32Array; readonly foilOutline: Float32Array;
}
export type ViewerEvent = ViewerSnapshot | ViewerStatusEvent | ShutdownAcknowledgement;
