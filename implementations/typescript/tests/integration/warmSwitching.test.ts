import {readFile} from "node:fs/promises";
import {resolve} from "node:path";
import {describe, expect, it} from "vitest";
import type {SolverId} from "../../src/core/contracts.js";
import {controlAt, parseScenario} from "../../src/core/scenario.js";
import {createSolver} from "../../src/solvers/factory.js";
import {ViewerModel} from "../../src/viewer/model.js";

const ids: readonly SolverId[] = ["stable-fluids", "lbm-d2q9", "pic-flip"];

describe("all directed TypeScript warm swaps", () => {
  for (const angleDegrees of [14, 25] as const) for (const sourceId of ids) for (const destinationId of ids) if (sourceId !== destinationId) it(`${sourceId} -> ${destinationId} at ${String(angleDegrees)} degrees`, async () => {
    const schema = JSON.parse(await readFile(resolve("../../spec/scenario.schema.json"), "utf8")) as object;
    const raw = JSON.parse(await readFile(resolve("../../scenarios/airfoil/default.json"), "utf8")) as unknown;
    const loaded = parseScenario(raw, schema); const scenario = {...loaded, domain: {...loaded.domain, resolution: [40, 24]}, controls: [{time: 0, angleDegrees}]}; const control = controlAt(scenario, 0.01);
    const source = createSolver(sourceId); source.initialize(scenario, scenario.seed); source.advance(control, 0.01);
    const destination = createSolver(destinationId); destination.initialize(scenario, scenario.seed);
    expect(destination.importState(source.exportState(), control).status).toBe("accepted");
    destination.advance(controlAt(scenario, 0.02), 0.01);
    expect(destination.exportState().velocity.every(Number.isFinite)).toBe(true);
  });

  for (const angleDegrees of [14, 25] as const) for (const sourceId of ids) for (const destinationId of ids) if (sourceId !== destinationId) it(`viewer ${sourceId} -> ${destinationId} at ${String(angleDegrees)} degrees`, async () => {
    const schema = JSON.parse(await readFile(resolve("../../spec/scenario.schema.json"), "utf8")) as object;
    const raw = JSON.parse(await readFile(resolve("../../scenarios/airfoil/default.json"), "utf8")) as unknown;
    const loaded = parseScenario(raw, schema); const scenario = {...loaded, domain: {...loaded.domain, resolution: [32, 20]}, controls: [{time: 0, angleDegrees}]};
    const model = new ViewerModel(scenario, sourceId); model.step(); const before = model.time; const tracerCount = model.tracers.positions.length;
    expect(model.switchSolver(destinationId)).toBe(true);
    expect(model.time).toBeCloseTo(before + scenario.outputDt, 10);
    expect(model.tracers.positions.length).toBe(tracerCount);
    expect(model.solver.info.id).toBe(destinationId);
    expect(model.snapshot().status).toContain("switched");
  });
});
