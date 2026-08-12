import {readFile} from "node:fs/promises";
import {dirname, join, resolve} from "node:path";
import {fileURLToPath} from "node:url";
import type {SolverId} from "../core/contracts.js";
import {parseScenario} from "../core/scenario.js";
import {createSolver} from "../solvers/factory.js";

const root = resolve(dirname(fileURLToPath(import.meta.url)), "../../../..");
const fixture = JSON.parse(await readFile(join(root, "spec/conformance/fullsize-acceptance.json"), "utf8")) as {warm_switch: {scenario: string; resolution: readonly [number, number]; angles_degrees: readonly number[]}};
const schema = JSON.parse(await readFile(join(root, "spec/scenario.schema.json"), "utf8")) as object;
const scenario = parseScenario(JSON.parse(await readFile(join(root, fixture.warm_switch.scenario), "utf8")) as unknown, schema);
if (scenario.domain.resolution[0] !== fixture.warm_switch.resolution[0] || scenario.domain.resolution[1] !== fixture.warm_switch.resolution[1]) throw new Error("warm-switch fixture resolution disagrees with its scenario");
const identifiers: readonly SolverId[] = ["stable-fluids", "lbm-d2q9", "pic-flip"];
for (const angleDegrees of fixture.warm_switch.angles_degrees) for (const sourceId of identifiers) for (const destinationId of identifiers) {
  if (sourceId === destinationId) continue;
  const source = createSolver(sourceId); source.initialize(scenario, scenario.seed);
  const sourceControl = {time: scenario.outputDt, angleDegrees, angularVelocityDegrees: 0}; source.advance(sourceControl, scenario.outputDt);
  const destination = createSolver(destinationId); destination.initialize(scenario, scenario.seed);
  const imported = destination.importState(source.exportState(), sourceControl); if (imported.status !== "accepted") throw new Error(`warm import rejected: ${sourceId} -> ${destinationId} at ${String(angleDegrees)}`);
  destination.advance({time: 2 * scenario.outputDt, angleDegrees, angularVelocityDegrees: 0}, scenario.outputDt);
  if (!destination.exportState().velocity.every(Number.isFinite)) throw new Error("warm switch produced non-finite state");
  console.log(`passed ${sourceId} -> ${destinationId} at ${angleDegrees.toFixed(1)} degrees`);
}
