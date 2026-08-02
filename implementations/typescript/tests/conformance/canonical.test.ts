import {readFile} from "node:fs/promises";
import {resolve} from "node:path";
import {describe, expect, it} from "vitest";
import {decodeNpy, semanticCOrder} from "../../src/core/npy.js";

describe("canonical NPY layouts", () => {
  it("loads equivalent C and Fortran semantic arrays", async () => {
    const cBytes = await readFile(resolve("../../spec/conformance/canonical-state-f32/velocity.npy"));
    const fBytes = await readFile(resolve("../../spec/conformance/canonical-state-f32-fortran/velocity.npy"));
    const c = decodeNpy(cBytes.buffer.slice(cBytes.byteOffset, cBytes.byteOffset + cBytes.byteLength));
    const f = decodeNpy(fBytes.buffer.slice(fBytes.byteOffset, fBytes.byteOffset + fBytes.byteLength));
    expect(c.shape).toEqual([1, 3, 4, 2]);
    expect(f.fortranOrder).toBe(true);
    expect(Array.from(semanticCOrder(f))).toEqual(Array.from(semanticCOrder(c)));
  });
});
