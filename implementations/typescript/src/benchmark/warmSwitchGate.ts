import {readFile} from "node:fs/promises";
import {dirname, join, resolve} from "node:path";
import {fileURLToPath} from "node:url";
import type {CanonicalFlowState, ControlState, Diagnostics, FlowSolver, FloatArray, ImportOutcome, ImportReason, RestartState, ReynoldsOutcome, Scenario, SolverId, SolverInfo, StepReport} from "../core/contracts.js";
import {NumericalFailure} from "../core/contracts.js";
import {parseScenario} from "../core/scenario.js";
import {createSolver} from "../solvers/factory.js";
import {ViewerModel} from "../viewer/model.js";

class DelegatingSolver implements FlowSolver {
  public constructor(protected readonly inner: FlowSolver) {}
  public get info(): SolverInfo { return this.inner.info; }
  public get reynolds(): number { return this.inner.reynolds; }
  public get stateRevision(): number { return this.inner.stateRevision; }
  public initialize(scenario: Scenario, seed: number): void { this.inner.initialize(scenario, seed); }
  public restart(scenario: Scenario, seed: number, start: RestartState): void { this.inner.restart(scenario, seed, start); }
  public setReynolds(reynolds: number): ReynoldsOutcome { return this.inner.setReynolds(reynolds); }
  public advance(control: ControlState, targetDt: number): StepReport { return this.inner.advance(control, targetDt); }
  public sampleVelocity(points: FloatArray): FloatArray { return this.inner.sampleVelocity(points); }
  public exportState(): CanonicalFlowState { return this.inner.exportState(); }
  public importState(state: CanonicalFlowState, control: ControlState): ImportOutcome { return this.inner.importState(state, control); }
  public diagnostics(): Diagnostics { return this.inner.diagnostics(); }
}

class RejectingImportSolver extends DelegatingSolver {
  public constructor(inner: FlowSolver, private readonly reason: Exclude<ImportReason, "none"> = "nonfinite_state") { super(inner); }
  public override importState(state: CanonicalFlowState, control: ControlState): ImportOutcome {
    void state; void control;
    return {status: "rejected", reason: this.reason, stage: "canonical-import", evidence: {}, discardedState: [], warnings: []};
  }
}

class FailingAdvanceSolver extends DelegatingSolver {
  public override advance(control: ControlState, targetDt: number): StepReport {
    void control; void targetDt;
    throw new NumericalFailure("stability_limit", "injected fresh-fallback validation failure", "time-mapping");
  }
}

const root = resolve(dirname(fileURLToPath(import.meta.url)), "../../../..");
const fixture = JSON.parse(await readFile(join(root, "spec/conformance/fullsize-acceptance.json"), "utf8")) as {warm_switch: {scenario: string; resolution: readonly [number, number]; angles_degrees: readonly number[]; require_all_directed_pairs: boolean; validate_fresh_fallback_first_step: boolean}};
const schema = JSON.parse(await readFile(join(root, "spec/schemas/scenario.schema.json"), "utf8")) as object;
const gate = fixture.warm_switch;
const scenario = parseScenario(JSON.parse(await readFile(join(root, gate.scenario), "utf8")) as unknown, schema);
if (scenario.domain.resolution[0] !== gate.resolution[0] || scenario.domain.resolution[1] !== gate.resolution[1]) throw new Error("warm-switch fixture resolution disagrees with its scenario");
if (!gate.require_all_directed_pairs) throw new Error("Revision 4 requires every directed warm-switch pair");
if (!gate.validate_fresh_fallback_first_step) throw new Error("Revision 4 requires tentative fresh-fallback validation");
const identifiers: readonly SolverId[] = ["stable-fluids", "lbm-d2q9", "pic-flip"];

for (const angleDegrees of gate.angles_degrees) for (const sourceId of identifiers) for (const destinationId of identifiers) {
  if (sourceId === destinationId) continue;
  const model = new ViewerModel(scenario, sourceId);
  model.setAngle(angleDegrees, 1);
  model.step();
  if (!model.switchSolver(destinationId)) throw new Error(`warm switch rejected: ${sourceId} -> ${destinationId} at ${String(angleDegrees)}`);
  if (!model.solver.exportState().velocity.every(Number.isFinite)) throw new Error("warm switch produced non-finite state");
  console.log(`passed warm ${sourceId} -> ${destinationId} at ${angleDegrees.toFixed(1)} degrees`);
}

function arraysEqual(left: ArrayLike<number>, right: ArrayLike<number>): boolean {
  if (left.length !== right.length) return false;
  for (let index = 0; index < left.length; index += 1) if (left[index] !== right[index]) return false;
  return true;
}

function validateFallback(angleDegrees: number, sourceId: SolverId, destinationId: SolverId, failFreshStep: boolean): void {
  let destinationCreations = 0;
  const factory = (id: SolverId): FlowSolver => {
    const solver = createSolver(id);
    if (id !== destinationId) return solver;
    destinationCreations += 1;
    if (destinationCreations === 1) return new RejectingImportSolver(solver);
    return failFreshStep ? new FailingAdvanceSolver(solver) : solver;
  };
  const model = new ViewerModel(scenario, sourceId, factory);
  model.setAngle(angleDegrees, 1);
  model.step();
  const source = model.solver;
  const sourceTime = model.time;
  const sourceEpoch = model.solverEpoch;
  const sourcePositions = model.tracers.positions.slice();
  const sourcePaths = model.snapshot().pathSegments.slice();
  const sourceCounters = model.tracers.recycleCounters;
  const sourceReynolds = model.solver.reynolds;
  const accepted = model.switchSolver(destinationId);
  const snapshot = model.snapshot();
  if (failFreshStep) {
    if (accepted) throw new Error("fresh fallback committed a failing tentative destination");
    if (model.solver !== source) throw new Error("failed fresh fallback replaced the valid source");
    if (model.time !== sourceTime || model.solverEpoch !== sourceEpoch) throw new Error("failed fresh fallback changed time or epoch");
    if (!arraysEqual(model.tracers.positions, sourcePositions) || !arraysEqual(snapshot.pathSegments, sourcePaths)) throw new Error("failed fresh fallback changed tracer presentation state");
    if (JSON.stringify(model.tracers.recycleCounters) !== JSON.stringify(sourceCounters)) throw new Error("failed fresh fallback changed tracer counters");
  } else {
    if (!accepted || snapshot.solverId !== destinationId) throw new Error("fresh fallback did not commit its validated destination");
    if (model.time <= sourceTime || model.solverEpoch !== sourceEpoch + 1) throw new Error("successful fresh fallback did not commit one destination step");
    if (snapshot.recoveryReason !== "nonfinite_state" || snapshot.recoveryStage !== "warm-import-fallback") throw new Error("successful fresh fallback omitted recovery telemetry");
    if (model.solver.reynolds !== sourceReynolds) throw new Error("successful fresh fallback changed selected Reynolds number");
    if (!model.solver.exportState().velocity.every(Number.isFinite)) throw new Error("fresh fallback produced a non-finite state");
    if (model.tracers.recycleCounters.forced_recovery !== sourceCounters.forced_recovery + model.tracers.count) throw new Error("successful fresh fallback did not reseed tracers exactly once");
  }
  if (destinationCreations !== 2) throw new Error("fresh fallback did not construct exactly two destinations");
}

for (const angleDegrees of gate.angles_degrees) for (let index = 0; index < identifiers.length; index += 1) {
  const destinationId = identifiers[index];
  const sourceId = identifiers[(index + 1) % identifiers.length];
  if (destinationId === undefined || sourceId === undefined) throw new Error("solver roster indexing failed");
  validateFallback(angleDegrees, sourceId, destinationId, false);
  validateFallback(angleDegrees, sourceId, destinationId, true);
  console.log(`passed fresh fallback transactions -> ${destinationId} at ${angleDegrees.toFixed(1)} degrees`);
}
