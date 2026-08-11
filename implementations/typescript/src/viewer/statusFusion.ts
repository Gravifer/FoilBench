import type {ViewerSnapshot, ViewerStatusEvent} from "./protocol.js";

export interface FusedViewerStatus {
  readonly status: string;
  readonly phase: ViewerSnapshot["phase"];
  readonly recoveryEpoch: number;
  readonly pendingStatus: string | null;
}

/**
 * Merge control-plane progress into an immutable physical snapshot only when
 * both messages identify the same committed solver state. Newer status for a
 * reset, recovery, or solver step remains visibly pending until its frame lands.
 */
export function fuseViewerStatus(
  snapshot: ViewerSnapshot,
  candidate: ViewerStatusEvent | null,
): FusedViewerStatus {
  if (candidate === null) {
    return {
      status: snapshot.status,
      phase: snapshot.phase,
      recoveryEpoch: snapshot.recoveryEpoch,
      pendingStatus: null,
    };
  }
  const samePhysicalState =
    candidate.solverEpoch === snapshot.solverEpoch
    && candidate.solverStateRevision === snapshot.solverStateRevision
    && candidate.recoveryEpoch === snapshot.recoveryEpoch;
  const currentOrNewerCommand = candidate.appliedCommand >= snapshot.appliedCommand;
  if (samePhysicalState && currentOrNewerCommand) {
    return {
      status: candidate.status,
      phase: candidate.phase,
      recoveryEpoch: candidate.recoveryEpoch,
      pendingStatus: null,
    };
  }
  const observablyNewer =
    candidate.appliedCommand > snapshot.appliedCommand
    || candidate.solverEpoch > snapshot.solverEpoch
    || (
      candidate.solverEpoch === snapshot.solverEpoch
      && candidate.solverStateRevision > snapshot.solverStateRevision
    )
    || candidate.recoveryEpoch > snapshot.recoveryEpoch;
  return {
    status: snapshot.status,
    phase: snapshot.phase,
    recoveryEpoch: snapshot.recoveryEpoch,
    pendingStatus: observablyNewer ? `pending frame: ${candidate.status}` : null,
  };
}
