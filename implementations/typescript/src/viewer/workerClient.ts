import type {Scenario, SolverId} from "../core/contracts.js";
import type {
  SolverBackend,
  ViewerPresentationProfile,
  ViewerStartState,
  ViewerCommandInput,
  ViewerEvent,
  ViewerSnapshot,
  ViewerStatusEvent,
} from "./protocol.js";

export interface ViewerClientState {
  readonly snapshot: ViewerSnapshot | null;
  readonly status: ViewerStatusEvent | null;
  readonly error: string | null;
}

export type ViewerClientListener = (state: ViewerClientState) => void;

export class ViewerWorkerClient {
  private sequence = 0;
  private snapshot: ViewerSnapshot | null = null;
  private status: ViewerStatusEvent | null = null;
  private error: string | null = null;
  private pendingPose: Extract<ViewerCommandInput, {readonly kind: "set-angle"}> | null = null;
  private poseFrame = 0;
  private shutdownAcknowledged = false;
  private readonly listeners = new Set<ViewerClientListener>();

  public constructor(private readonly worker = new Worker(new URL("../worker/simulationWorker.ts", import.meta.url), {type: "module"})) {
    this.worker.onmessage = (event: MessageEvent<ViewerEvent>): void => {
      if (event.data.kind === "shutdown-ack") {
        this.shutdownAcknowledged = true;
        return;
      }
      if (event.data.kind === "status") this.status = event.data;
      else this.snapshot = event.data;
      this.error = null;
      this.notify();
    };
    this.worker.onerror = (event): void => {
      this.error = `worker failure: ${event.message}`;
      this.notify();
    };
  }

  public get latest(): ViewerSnapshot | null { return this.snapshot; }
  public get latestStatus(): ViewerStatusEvent | null { return this.status; }

  public subscribe(listener: ViewerClientListener): () => void {
    this.listeners.add(listener);
    listener({snapshot: this.snapshot, status: this.status, error: this.error});
    return (): void => { this.listeners.delete(listener); };
  }

  public initialize(scenario: Scenario, solverId: SolverId, backend: SolverBackend, startState?: ViewerStartState, presentationProfile: ViewerPresentationProfile = "reference"): void {
    const command: ViewerCommandInput = startState === undefined
      ? {kind: "initialize", scenario, solverId, backend, presentationProfile}
      : {kind: "initialize", scenario, solverId, backend, presentationProfile, startState};
    this.send(command, true);
  }

  public send(command: ViewerCommandInput, allowBeforeReady = false): void {
    if (!allowBeforeReady && this.snapshot === null && command.kind !== "shutdown") return;
    this.flushPose();
    this.sequence += 1;
    this.worker.postMessage({...command, sequence: this.sequence});
  }

  public queueAngle(angleDegrees: number, timestamp = performance.now()): void {
    if (this.snapshot === null) return;
    if (this.pendingPose === null) {
      this.sequence += 1;
      this.pendingPose = {kind: "set-angle", angleDegrees, timestamp};
    } else {
      this.pendingPose = {...this.pendingPose, angleDegrees, timestamp};
    }
    if (this.poseFrame === 0) this.poseFrame = requestAnimationFrame(() => { this.flushPose(); });
  }

  public releaseAngle(): void {
    if (this.poseFrame !== 0) cancelAnimationFrame(this.poseFrame);
    this.flushPose();
    this.send({kind: "release-angle"});
  }

  public acknowledgeSnapshot(revision: number): void {
    this.worker.postMessage({kind: "snapshot-consumed", revision});
  }

  public setVisible(visible: boolean): void {
    this.send({kind: "visibility", visible});
  }

  public shutdown(): void {
    if (!this.shutdownAcknowledged) this.send({kind: "shutdown"}, true);
  }

  public terminate(): void {
    this.worker.terminate();
    this.listeners.clear();
  }

  private flushPose(): void {
    this.poseFrame = 0;
    const pose = this.pendingPose;
    this.pendingPose = null;
    if (pose !== null) this.worker.postMessage({...pose, sequence: this.sequence});
  }

  private notify(): void {
    const state = {snapshot: this.snapshot, status: this.status, error: this.error};
    for (const listener of this.listeners) listener(state);
  }
}
