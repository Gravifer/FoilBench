import {readFile} from "node:fs/promises";
import {resolve} from "node:path";
import {expect, it} from "vitest";
import type {DomainSpec} from "../../src/core/contracts.js";
import {wakeMetrics} from "../../src/core/metrics.js";

interface WakeFixture {
  readonly bounds: readonly (readonly [number, number])[];
  readonly resolution: readonly number[];
  readonly pivot_x: number;
  readonly chord: number;
  readonly freestream_u: number;
  readonly velocity: readonly (readonly (readonly [number, number])[])[];
  readonly solid: readonly (readonly boolean[])[];
  readonly expected: Readonly<{wake_width: number; recirculation_area: number}>;
}

it("matches the shared wake-metrics fixture", async () => {
  const fixture = JSON.parse(
    await readFile(resolve("../../spec/conformance/wake-metrics.json"), "utf8"),
  ) as WakeFixture;
  const domain: DomainSpec = {
    dimension: 2,
    bounds: fixture.bounds,
    resolution: fixture.resolution,
    periodicAxes: [],
  };
  const velocity = new Float64Array(fixture.velocity.flat(2));
  const solid = new Uint8Array(fixture.solid.flat().map((value) => value ? 1 : 0));

  expect(wakeMetrics(
    velocity,
    domain,
    fixture.pivot_x,
    fixture.chord,
    fixture.freestream_u,
    solid,
  )).toEqual(fixture.expected);
});
