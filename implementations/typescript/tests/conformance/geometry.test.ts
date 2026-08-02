import {readFile} from "node:fs/promises";
import {resolve} from "node:path";
import {describe, expect, it} from "vitest";
import {NacaFoil} from "../../src/core/geometry.js";

interface Fixture {
  foil: {naca: string; chord: number; pivot: number[]};
  queries: {angle_degrees: number; points: number[][]; signed_distance: number[]; normals: number[][]}[];
  absolute_tolerances: {signed_distance: number; normal: number};
}

describe("shared NACA geometry", () => {
  it("matches signed-distance and normal samples", async () => {
    const fixture = JSON.parse(await readFile(resolve("../../spec/conformance/naca2412.json"), "utf8")) as Fixture;
    const foil = new NacaFoil(fixture.foil);
    for (const query of fixture.queries) for (let index = 0; index < query.points.length; index += 1) {
      const point = query.points[index]; const expectedDistance = query.signed_distance[index]; const expectedNormal = query.normals[index];
      if (point === undefined || expectedDistance === undefined || expectedNormal === undefined) throw new Error("invalid fixture");
      const x = point[0]; const y = point[1];
      if (x === undefined || y === undefined) throw new Error("invalid point");
      expect(foil.signedDistance(x, y, query.angle_degrees)).toBeCloseTo(expectedDistance, 9);
      const actual = foil.normal(x, y, query.angle_degrees);
      expect(Math.abs(actual[0] - (expectedNormal[0] ?? 0))).toBeLessThanOrEqual(fixture.absolute_tolerances.normal);
      expect(Math.abs(actual[1] - (expectedNormal[1] ?? 0))).toBeLessThanOrEqual(fixture.absolute_tolerances.normal);
    }
  });
});
