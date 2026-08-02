import {readFile} from "node:fs/promises";
import {resolve} from "node:path";
import {describe, expect, it} from "vitest";
import type {SolverId} from "../../src/core/contracts.js";
import {controlAt, parseScenario} from "../../src/core/scenario.js";
import {createSolver} from "../../src/solvers/factory.js";

const ids: readonly SolverId[] = ["stable-fluids", "lbm-d2q9", "pic-flip"];

describe("all directed TypeScript warm swaps", () => {
  for (const sourceId of ids) for (const destinationId of ids) if (sourceId !== destinationId) it(`${sourceId} -> ${destinationId}`, async () => {
    const schema = JSON.parse(await readFile(resolve("../../spec/scenario.schema.json"), "utf8")) as object;
    const raw = JSON.parse(await readFile(resolve("../../scenarios/validation/uniform.json"), "utf8")) as unknown;
    const scenario = parseScenario(raw, schema); const control = controlAt(scenario, 0.01);
    const source = createSolver(sourceId); source.initialize(scenario, scenario.seed); source.advance(control, 0.01);
    const destination = createSolver(destinationId); destination.initialize(scenario, scenario.seed);
    expect(destination.importState(source.exportState(), control).status).toBe("accepted");
    destination.advance(controlAt(scenario, 0.02), 0.01);
    expect(destination.exportState().velocity.every(Number.isFinite)).toBe(true);
  });
});
