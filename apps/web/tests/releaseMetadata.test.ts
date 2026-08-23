import {describe, expect, it} from "vitest";

import {createReleaseMetadata, isPrereleaseVersion, isReleaseVersion} from "../releaseMetadata.js";

const contract = {contractId: "foilbench-phase3-v1", revision: 5} as const;

describe("release metadata", () => {
  it.each(["v0.2.0", "v1.0.0-alpha", "v1.2.3-rc1", "v1.2.3-rc.1", "v10.20.30-alpha.2"])(
    "accepts the supported SemVer spelling %s",
    (version) => expect(isReleaseVersion(version)).toBe(true),
  );

  it.each(["1.2.3", "v1.2", "v01.2.3", "v1.02.3", "v1.2.3-01", "v1.2.3+build.7", "v1.2.3-"])(
    "rejects the unsupported release spelling %s",
    (version) => expect(isReleaseVersion(version)).toBe(false),
  );

  it("derives prerelease state from the validated version", () => {
    expect(isPrereleaseVersion("v1.2.3")).toBe(false);
    expect(isPrereleaseVersion("v1.2.3-rc.1")).toBe(true);
    expect(() => isPrereleaseVersion("nightly")).toThrow(/invalid release version/);
  });

  it("emits non-release identity for ordinary local builds", () => {
    expect(createReleaseMetadata({}, contract, "/FoilBench/")).toEqual({
      schema_version: 1,
      release: false,
      version: null,
      commit: null,
      built_at: null,
      pages_base: "/FoilBench/",
      contract_id: contract.contractId,
      contract_revision: contract.revision,
    });
  });

  it("requires a complete and exact release identity", () => {
    const environment = {
      version: "v0.2.0-rc.1",
      commit: "0123456789abcdef0123456789abcdef01234567",
      builtAt: "2026-08-23T12:34:56Z",
    };
    expect(createReleaseMetadata(environment, contract, "/FoilBench/")).toMatchObject({
      release: true,
      version: environment.version,
      commit: environment.commit,
      built_at: environment.builtAt,
    });
    expect(() => createReleaseMetadata({version: environment.version}, contract, "/FoilBench/")).toThrow(/together/);
    expect(() => createReleaseMetadata({...environment, commit: "deadbeef"}, contract, "/FoilBench/")).toThrow(/full lowercase/);
    expect(() => createReleaseMetadata(environment, contract, "/wrong/")).toThrow(/pages base/);
  });

  it.each([
    "2026-08-23",
    "2026-08-23T12:34:56",
    "2026-08-23T12:34:56+00:00",
    "2026-02-31T12:34:56Z",
    "soon",
  ])("rejects the non-canonical release build time %s", (builtAt) => {
    expect(() =>
      createReleaseMetadata(
        {
          version: "v0.2.0",
          commit: "0123456789abcdef0123456789abcdef01234567",
          builtAt,
        },
        contract,
        "/FoilBench/",
      ),
    ).toThrow(/UTC ISO-8601/);
  });
});
