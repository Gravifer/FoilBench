export const RELEASE_VERSION_PATTERN = /^v(0|[1-9]\d*)\.(0|[1-9]\d*)\.(0|[1-9]\d*)(?:-(?:(?:0|[1-9]\d*)|(?:\d*[A-Za-z-][0-9A-Za-z-]*))(?:\.(?:(?:0|[1-9]\d*)|(?:\d*[A-Za-z-][0-9A-Za-z-]*)))*)?$/;

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
  if (!Number.isFinite(Date.parse(environment.builtAt))) throw new Error("release build time must be an ISO-8601 timestamp");
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
