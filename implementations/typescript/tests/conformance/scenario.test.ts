import {readFile} from "node:fs/promises";
import {resolve} from "node:path";
import {describe, expect, it} from "vitest";
import {controlAt, parseScenario} from "../../src/core/scenario.js";

describe("shared scenario", () => {
  it("loads the default scenario and smooth controls", async () => {
    const schema = JSON.parse(await readFile(resolve("../../spec/scenario.schema.json"), "utf8")) as object;
    const document = JSON.parse(await readFile(resolve("../../scenarios/airfoil/default.json"), "utf8")) as unknown;
    const scenario = parseScenario(document, schema);
    expect(scenario.domain.resolution).toEqual([160, 96]);
    expect(scenario.solverOptions.stableAdvection).toBe("maccormack");
    expect(Number.isFinite(controlAt(scenario, 3).angularVelocityDegrees)).toBe(true);
  });
});
