import {readFile} from "node:fs/promises";
import {resolve} from "node:path";
import {describe, expect, it} from "vitest";
import {NacaFoil} from "../../src/core/geometry.js";
import {parseScenario, validateDocument} from "../../src/core/scenario.js";
import {lbmSpongeStrength} from "../../src/solvers/lbm.js";

type JsonObject = Record<string, unknown>;
type PathComponent = string | number;

interface NegativeCase {
  readonly id: string;
  readonly base: string;
  readonly path: PathComponent[];
  readonly value: unknown;
  readonly reason: string;
}

async function json(path: string): Promise<JsonObject> {
  return JSON.parse(await readFile(resolve(path), "utf8")) as JsonObject;
}

function setPath(document: JsonObject, path: readonly PathComponent[], value: unknown): void {
  let cursor: unknown = document;
  for (const component of path.slice(0, -1)) {
    if (typeof component === "number") {
      if (!Array.isArray(cursor)) throw new TypeError("numeric fixture path needs an array");
      cursor = cursor[component];
    } else {
      if (cursor === null || typeof cursor !== "object" || Array.isArray(cursor)) throw new TypeError("named fixture path needs an object");
      cursor = (cursor as JsonObject)[component];
    }
  }
  const final = path.at(-1);
  if (final === undefined) throw new TypeError("fixture path cannot be empty");
  if (typeof final === "number") {
    if (!Array.isArray(cursor)) throw new TypeError("numeric fixture path needs an array");
    cursor[final] = value;
  } else {
    if (cursor === null || typeof cursor !== "object" || Array.isArray(cursor)) throw new TypeError("named fixture path needs an object");
    (cursor as JsonObject)[final] = value;
  }
}

describe("Revision 5 conformance fixtures", () => {
  it("matches transformed geometry, wall motion, and radius", async () => {
    const fixture = await json("../../spec/conformance/geometry-v1.json");
    const descriptor = fixture["descriptor"] as {naca: string; chord: number; pivot: number[]};
    const foil = new NacaFoil(descriptor);
    const tolerances = fixture["absolute_tolerances"] as Record<string, number>;
    const surfaceX = fixture["surface_x"] as number[];
    const expectedUpper = fixture["surface_upper"] as number[];
    const expectedLower = fixture["surface_lower"] as number[];
    expect(new Set([surfaceX.length, expectedUpper.length, expectedLower.length]).size).toBe(1);
    for (let index = 0; index < surfaceX.length; index += 1) {
      const [upper, lower] = foil.surface(surfaceX[index] ?? 0);
      expect(Math.abs(upper - (expectedUpper[index] ?? 0))).toBeLessThanOrEqual(tolerances["surface"] ?? 0);
      expect(Math.abs(lower - (expectedLower[index] ?? 0))).toBeLessThanOrEqual(tolerances["surface"] ?? 0);
    }
    const points = fixture["points"] as number[][];
    const distances = fixture["signed_distance"] as number[];
    const normals = fixture["normals"] as number[][];
    const contains = fixture["contains"] as boolean[];
    const velocities = fixture["wall_velocity"] as number[][];
    expect(new Set([points.length, distances.length, normals.length, contains.length, velocities.length]).size).toBe(1);
    const angle = fixture["angle_degrees"] as number;
    const angularVelocity = fixture["angular_velocity_degrees"] as number;
    for (let index = 0; index < points.length; index += 1) {
      const point = points[index];
      if (point === undefined) throw new TypeError("geometry fixture point missing");
      const x = point[0] ?? 0; const y = point[1] ?? 0;
      expect(Math.abs(foil.signedDistance(x, y, angle) - (distances[index] ?? 0))).toBeLessThanOrEqual(tolerances["signed_distance"] ?? 0);
      const actualNormal = foil.normal(x, y, angle); const expectedNormal = normals[index] ?? [];
      expect(Math.abs(actualNormal[0] - (expectedNormal[0] ?? 0))).toBeLessThanOrEqual(tolerances["normal"] ?? 0);
      expect(Math.abs(actualNormal[1] - (expectedNormal[1] ?? 0))).toBeLessThanOrEqual(tolerances["normal"] ?? 0);
      expect(foil.contains(x, y, angle)).toBe(contains[index]);
      const actualVelocity = foil.wallVelocity(x, y, angularVelocity); const expectedVelocity = velocities[index] ?? [];
      expect(Math.abs(actualVelocity[0] - (expectedVelocity[0] ?? 0))).toBeLessThanOrEqual(tolerances["wall_velocity"] ?? 0);
      expect(Math.abs(actualVelocity[1] - (expectedVelocity[1] ?? 0))).toBeLessThanOrEqual(tolerances["wall_velocity"] ?? 0);
    }
    expect(Math.abs(foil.maximumRadius - (fixture["maximum_radius"] as number))).toBeLessThanOrEqual(tolerances["radius"] ?? 0);
  });

  it("consumes canonical-v2, fidelity, MAC, and LBM identities", async () => {
    const manifest = await json("../../spec/conformance/canonical-manifest-v2.json");
    const manifestSchema = await json("../../spec/schemas/canonical-manifest-v2.schema.json");
    validateDocument(manifest, manifestSchema);
    expect((manifest["geometry"] as JsonObject)["family"]).toBe("naca-four-digit-v1");
    expect(manifest["producer"]).toEqual({implementation: "rust", execution_target: "native", build: null});

    const fidelity = await json("../../spec/conformance/fidelity-cases.json");
    const scenarioSchema = await json("../../spec/schemas/scenario.schema.json");
    for (const value of fidelity["cases"] as JsonObject[]) {
      const scenario = parseScenario(await json(`../../${String(value["scenario"])}`), scenarioSchema);
      expect((value["resolution"] as number[]).length).toBe(scenario.domain.dimension);
      expect(value["metrics"]).toBeTruthy();
    }

    const mac = await json("../../spec/conformance/mac-boundary.json");
    expect(mac).toEqual({schema_version: 1, x_nonperiodic: "prescribed-inlet-zero-gradient-outlet", y_freestream: "prescribed-freestream-normal-and-tangential", y_poiseuille: "no-slip-channel-wall", periodic_duplicate: "endpoint-average"});
    const lbm = await json("../../spec/conformance/lbm-boundary.json");
    const sponge = lbm["sponge"] as Record<string, number>;
    expect(lbmSpongeStrength(160, 96, 80, 0, false, false)).toBeCloseTo(sponge["transverse_maximum"] ?? 0, 14);
    expect(lbmSpongeStrength(160, 96, 159, 48, false, false)).toBeCloseTo(sponge["outlet_maximum"] ?? 0, 14);
  });

  it("rejects every shared negative scenario before solver initialization", async () => {
    const fixture = await json("../../spec/conformance/scenario-negative.json");
    const schema = await json("../../spec/schemas/scenario.schema.json");
    for (const scenarioCase of fixture["cases"] as NegativeCase[]) {
      const document = structuredClone(await json(`../../${scenarioCase.base}`));
      setPath(document, scenarioCase.path, scenarioCase.value);
      expect(() => parseScenario(document, schema), scenarioCase.id).toThrow();
    }
  });
});
