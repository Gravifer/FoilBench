import {readFile} from "node:fs/promises";
import {resolve} from "node:path";
import {describe, expect, it} from "vitest";
import {Pcg32} from "../../src/core/rng.js";

interface Fixture {cases: {seed: number; stream: number; uint32: number[]; float32_bits: string[]}[]}

describe("shared PCG32 vectors", () => {
  it("matches integer and Float32 bit streams", async () => {
    const fixture = JSON.parse(await readFile(resolve("../../spec/conformance/pcg32.json"), "utf8")) as Fixture;
    for (const testCase of fixture.cases) {
      const integers = new Pcg32(testCase.seed, testCase.stream);
      expect(testCase.uint32.map(() => integers.nextUint32())).toEqual(testCase.uint32);
      const floats = new Pcg32(testCase.seed, testCase.stream);
      const bits = new Uint32Array(1); const value = new Float32Array(bits.buffer);
      const actual = testCase.float32_bits.map(() => { value[0] = floats.nextFloat32(); return `0x${(bits[0] ?? 0).toString(16).padStart(8, "0")}`; });
      expect(actual).toEqual(testCase.float32_bits);
    }
  });
});
