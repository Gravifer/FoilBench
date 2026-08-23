export const RELEASE_VERSION_PATTERN = /^v(0|[1-9]\d*)\.(0|[1-9]\d*)\.(0|[1-9]\d*)(?:-(?:(?:0|[1-9]\d*)|(?:\d*[A-Za-z-][0-9A-Za-z-]*))(?:\.(?:(?:0|[1-9]\d*)|(?:\d*[A-Za-z-][0-9A-Za-z-]*)))*)?$/;
const RELEASE_BUILT_AT_PATTERN = /^\d{4}-(?:0[1-9]|1[0-2])-(?:0[1-9]|[12]\d|3[01])T(?:[01]\d|2[0-3]):[0-5]\d:[0-5]\dZ$/;

export interface ContractIdentity {
  readonly contractId: string;
  readonly revision: number;
}

export interface ReleaseEnvironment {
  readonly version?: string | undefined;
  readonly commit?: string | undefined;
  readonly builtAt?: string | undefined;
}

export interface ReleaseMetadata {
  readonly schema_version: 1;
  readonly release: boolean;
  readonly version: string | null;
  readonly commit: string | null;
  readonly built_at: string | null;
  readonly pages_base: string;
  readonly contract_id: string;
  readonly contract_revision: number;
}

export function isReleaseVersion(value: string): boolean {
  return RELEASE_VERSION_PATTERN.test(value);
}

export function isPrereleaseVersion(value: string): boolean {
  if (!isReleaseVersion(value)) throw new Error(`invalid release version ${JSON.stringify(value)}`);
  return value.includes("-");
}

function isReleaseBuildTime(value: string): boolean {
  if (!RELEASE_BUILT_AT_PATTERN.test(value)) return false;
  const timestamp = Date.parse(value);
  return Number.isFinite(timestamp) && new Date(timestamp).toISOString() === `${value.slice(0, -1)}.000Z`;
}

export function createReleaseMetadata(
  environment: ReleaseEnvironment,
  contract: ContractIdentity,
  pagesBase: string,
): ReleaseMetadata {
  const supplied = [environment.version, environment.commit, environment.builtAt].filter((value) => value !== undefined);
  if (supplied.length === 0) {
    return {
      schema_version: 1,
      release: false,
      version: null,
      commit: null,
      built_at: null,
      pages_base: pagesBase,
      contract_id: contract.contractId,
      contract_revision: contract.revision,
    };
  }
  if (supplied.length !== 3 || environment.version === undefined || environment.commit === undefined || environment.builtAt === undefined) {
    throw new Error("release builds require version, commit, and build time together");
  }
  if (!isReleaseVersion(environment.version)) throw new Error(`invalid release version ${JSON.stringify(environment.version)}`);
  if (!/^[0-9a-f]{40}$/.test(environment.commit)) throw new Error("release commit must be a full lowercase Git SHA");
  if (!isReleaseBuildTime(environment.builtAt)) {
    throw new Error("release build time must be a UTC ISO-8601 timestamp");
  }
  if (pagesBase !== "/FoilBench/") throw new Error(`release pages base must be /FoilBench/, received ${JSON.stringify(pagesBase)}`);
  return {
    schema_version: 1,
    release: true,
    version: environment.version,
    commit: environment.commit,
    built_at: environment.builtAt,
    pages_base: pagesBase,
    contract_id: contract.contractId,
    contract_revision: contract.revision,
  };
}
