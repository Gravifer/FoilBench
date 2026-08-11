import {describe, expect, it} from "vitest";
import type {ViewerSnapshot, ViewerStatusEvent} from "../../src/viewer/protocol.js";
import {fuseViewerStatus} from "../../src/viewer/statusFusion.js";

const snapshot = {
  kind: "snapshot",
  revision: 4,
  appliedCommand: 8,
  solverEpoch: 2,
  solverStateRevision: 11,
  recoveryEpoch: 1,
  phase: "running",
  status: "frame status",
} as ViewerSnapshot;

function status(overrides: Partial<ViewerStatusEvent> = {}): ViewerStatusEvent {
  return {
    kind: "status",
    revision: 4,
    appliedCommand: 8,
    solverEpoch: 2,
    solverStateRevision: 11,
    recoveryEpoch: 1,
    phase: "paused",
    status: "control status",
    recoveryReason: null,
    recoveryStage: null,
    ...overrides,
  };
}

describe("viewer status fusion", () => {
  it("applies control-only status for the same committed solver state", () => {
    expect(fuseViewerStatus(snapshot, status())).toEqual({
      status: "control status",
      phase: "paused",
      recoveryEpoch: 1,
      pendingStatus: null,
    });
  });

  it("does not let stale status overwrite a newer frame", () => {
    expect(fuseViewerStatus(snapshot, status({appliedCommand: 7}))).toEqual({
      status: "frame status",
      phase: "running",
      recoveryEpoch: 1,
      pendingStatus: null,
    });
  });

  it("reports new solver state as pending without relabeling the old frame", () => {
    expect(fuseViewerStatus(snapshot, status({
      appliedCommand: 9,
      solverEpoch: 3,
      solverStateRevision: 0,
      recoveryEpoch: 2,
      status: "fresh restart complete",
    }))).toEqual({
      status: "frame status",
      phase: "running",
      recoveryEpoch: 1,
      pendingStatus: "pending frame: fresh restart complete",
    });
  });
});
