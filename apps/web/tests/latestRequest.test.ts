import {describe, expect, it} from "vitest";

import {LatestRequestGate} from "../src/latestRequest.js";

function deferred<T>(): {readonly promise: Promise<T>; readonly resolve: (value: T) => void; readonly reject: (reason: unknown) => void} {
  let resolve!: (value: T) => void;
  let reject!: (reason: unknown) => void;
  const promise = new Promise<T>((resolvePromise, rejectPromise) => {
    resolve = resolvePromise;
    reject = rejectPromise;
  });
  return {promise, resolve, reject};
}

describe("LatestRequestGate", () => {
  it("accepts only the newest request when completions arrive out of order", async () => {
    const gate = new LatestRequestGate();
    const older = deferred<string>();
    const newer = deferred<string>();
    const olderResult = gate.run(() => older.promise);
    const newerResult = gate.run(() => newer.promise);

    newer.resolve("newer");
    expect(await newerResult).toEqual({current: true, value: "newer"});
    older.resolve("older");
    expect(await olderResult).toEqual({current: false});
  });

  it("suppresses stale failures but preserves the current failure", async () => {
    const gate = new LatestRequestGate();
    const stale = deferred<string>();
    const current = deferred<string>();
    const staleResult = gate.run(() => stale.promise);
    const currentResult = gate.run(() => current.promise);

    stale.reject(new Error("stale"));
    expect(await staleResult).toEqual({current: false});
    current.reject(new Error("current"));
    await expect(currentResult).rejects.toThrow("current");
  });
});
