import type {FailureEvidence, FailureReason, FailureStage, ImportOutcome} from "./contracts.js";

export function acceptedImport(discardedState: readonly string[] = [], warnings: readonly string[] = []): ImportOutcome {
  return {status: "accepted", reason: "none", stage: null, evidence: {}, discardedState, warnings};
}

export function rejectedImport(reason: FailureReason, stage: FailureStage, evidence: FailureEvidence = {}): ImportOutcome {
  return {status: "rejected", reason, stage, evidence, discardedState: [], warnings: []};
}
