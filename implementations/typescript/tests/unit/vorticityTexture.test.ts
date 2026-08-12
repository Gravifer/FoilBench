import {readFile} from "node:fs/promises";
import {resolve} from "node:path";
import {describe, expect, it} from "vitest";
import {vorticityRgba} from "../../src/viewer/vorticityTexture.js";

describe("vorticity texture orientation", () => {
  it("maps y-min to the canvas bottom without changing vorticity sign", async () => {
    const fixture = JSON.parse(
      await readFile(resolve("../../spec/conformance/vorticity-display.json"), "utf8"),
    ) as {index_zero_corner: string; vertical_reflection: boolean};
    expect(fixture.index_zero_corner).toBe("lower-left");
    expect(fixture.vertical_reflection).toBe(false);

    // Semantic field rows are bottom-to-top. Canvas scanlines are top-to-bottom.
    const rgba = vorticityRgba(new Float32Array([1, 0, -1, 0]), 2, 2);
    const canvasTopLeft = 0;
    const canvasBottomLeft = 2;
    expect(rgba[4 * canvasTopLeft + 2] ?? 0).toBeGreaterThan(rgba[4 * canvasTopLeft] ?? 0);
    expect(rgba[4 * canvasBottomLeft] ?? 0).toBeGreaterThan(rgba[4 * canvasBottomLeft + 2] ?? 0);
  });

  it("rejects dimensions that do not match the field", () => {
    expect(() => vorticityRgba(new Float32Array(3), 2, 2)).toThrow(RangeError);
  });
});
