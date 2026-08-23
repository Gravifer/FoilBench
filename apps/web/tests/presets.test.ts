import {readFileSync} from "node:fs";
import {resolve} from "node:path";
import {afterEach, describe, expect, it, vi} from "vitest";

afterEach(() => {
  vi.unstubAllGlobals();
  vi.resetModules();
});

describe("scenario schema loading", () => {
  it("retries after a failed schema response", async () => {
    const schema = JSON.parse(readFileSync(resolve(import.meta.dirname, "../../../spec/schemas/scenario.schema.json"), "utf8")) as object;
    const scenario = JSON.parse(readFileSync(resolve(import.meta.dirname, "../../../scenarios/airfoil/default.json"), "utf8")) as unknown;
    const fetchMock = vi.fn()
      .mockResolvedValueOnce(new Response(null, {status: 503}))
      .mockResolvedValueOnce(new Response(JSON.stringify(schema), {status: 200, headers: {"content-type": "application/json"}}));
    vi.stubGlobal("fetch", fetchMock);
    const {parseScenarioDocument} = await import("../src/presets.js");

    await expect(parseScenarioDocument(scenario)).rejects.toThrow("scenario schema failed to load (503)");
    await expect(parseScenarioDocument(scenario)).resolves.toMatchObject({id: "naca2412-dynamic"});
    expect(fetchMock).toHaveBeenCalledTimes(2);
  });
});
