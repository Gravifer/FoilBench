import {afterEach, describe, expect, it, vi} from "vitest";

import {fetchJsonResource} from "../../src/viewer/resourceFetch.js";

afterEach(() => {
  vi.unstubAllGlobals();
});

describe("viewer JSON resources", () => {
  it("reports the resource label and HTTP status before parsing", async () => {
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(new Response("not JSON", {status: 404, statusText: "Not Found"})));
    await expect(fetchJsonResource("/missing.json", "scenario schema")).rejects.toThrow("scenario schema failed to load (404 Not Found)");
  });

  it("distinguishes malformed JSON from transport failure", async () => {
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(new Response("<html></html>", {status: 200})));
    await expect(fetchJsonResource("/scenario.json", "scenario")).rejects.toThrow("scenario is not valid JSON");
  });

  it("returns a successfully decoded resource", async () => {
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(new Response('{"valid":true}', {status: 200})));
    await expect(fetchJsonResource<{readonly valid: boolean}>("/scenario.json", "scenario")).resolves.toEqual({valid: true});
  });
});
