"""Decide whether a published FoilBench release may replace GitHub Pages."""

from __future__ import annotations

import argparse
import json
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Final, TypeAlias, cast

_VERSION_PATTERN: Final = re.compile(
    r"^v(0|[1-9]\d*)\.(0|[1-9]\d*)\.(0|[1-9]\d*)"
    r"(?:-((?:(?:0|[1-9]\d*)|(?:\d*[A-Za-z-][0-9A-Za-z-]*))"
    r"(?:\.(?:(?:0|[1-9]\d*)|(?:\d*[A-Za-z-][0-9A-Za-z-]*)))*))?$"
)
_COMMIT_PATTERN: Final = re.compile(r"^[0-9a-f]{40}$")
_BUILT_AT_PATTERN: Final = re.compile(
    r"^\d{4}-(?:0[1-9]|1[0-2])-(?:0[1-9]|[12]\d|3[01])"
    r"T(?:[01]\d|2[0-3]):[0-5]\d:[0-5]\dZ$"
)
_METADATA_FIELDS: Final = frozenset(
    {
        "schema_version",
        "release",
        "version",
        "commit",
        "built_at",
        "pages_base",
        "contract_id",
        "contract_revision",
    }
)
_MAX_METADATA_BYTES: Final = 64 * 1024

PrereleaseIdentifier: TypeAlias = int | str


class PolicyError(ValueError):
    """The deployment policy could not reach a trustworthy decision."""


@dataclass(frozen=True, slots=True)
class Version:
    major: int
    minor: int
    patch: int
    prerelease: tuple[PrereleaseIdentifier, ...]


@dataclass(frozen=True, slots=True)
class DeploymentPlan:
    candidate: str
    deployed_version: str | None
    deploy: bool
    reason: str


def parse_version(value: str) -> Version:
    """Parse the repository's accepted SemVer subset."""
    match = _VERSION_PATTERN.fullmatch(value)
    if match is None:
        raise PolicyError(f"invalid release version: {value!r}")
    prerelease_text = match.group(4)
    identifiers: tuple[PrereleaseIdentifier, ...]
    if prerelease_text is None:
        identifiers = ()
    else:
        identifiers = tuple(
            int(identifier) if identifier.isdigit() else identifier
            for identifier in prerelease_text.split(".")
        )
    return Version(int(match.group(1)), int(match.group(2)), int(match.group(3)), identifiers)


def compare_versions(left: Version, right: Version) -> int:
    """Return the SemVer precedence comparison of two parsed versions."""
    left_core = (left.major, left.minor, left.patch)
    right_core = (right.major, right.minor, right.patch)
    if left_core != right_core:
        return 1 if left_core > right_core else -1
    if not left.prerelease and not right.prerelease:
        return 0
    if not left.prerelease:
        return 1
    if not right.prerelease:
        return -1
    for left_identifier, right_identifier in zip(left.prerelease, right.prerelease, strict=False):
        if left_identifier == right_identifier:
            continue
        if isinstance(left_identifier, int) and isinstance(right_identifier, str):
            return -1
        if isinstance(left_identifier, str) and isinstance(right_identifier, int):
            return 1
        return 1 if left_identifier > right_identifier else -1
    if len(left.prerelease) == len(right.prerelease):
        return 0
    return 1 if len(left.prerelease) > len(right.prerelease) else -1


def validate_deployed_metadata(value: object) -> str:
    """Validate live Pages identity and return its release version."""
    if not isinstance(value, dict):
        raise PolicyError("deployed release metadata must be a JSON object")
    metadata = cast(dict[str, object], value)
    if frozenset(metadata) != _METADATA_FIELDS:
        missing = sorted(_METADATA_FIELDS - frozenset(metadata))
        extra = sorted(frozenset(metadata) - _METADATA_FIELDS)
        raise PolicyError(f"deployed release metadata fields differ: missing={missing}, extra={extra}")
    if metadata["schema_version"] != 1 or metadata["release"] is not True:
        raise PolicyError("deployed metadata does not identify a versioned release build")
    version = metadata["version"]
    if not isinstance(version, str):
        raise PolicyError("deployed release version must be a string")
    parse_version(version)
    commit = metadata["commit"]
    if not isinstance(commit, str) or _COMMIT_PATTERN.fullmatch(commit) is None:
        raise PolicyError("deployed release commit must be a full lowercase Git SHA")
    built_at = metadata["built_at"]
    if not isinstance(built_at, str) or _BUILT_AT_PATTERN.fullmatch(built_at) is None:
        raise PolicyError("deployed release build time must be a UTC ISO-8601 timestamp")
    if metadata["pages_base"] != "/FoilBench/":
        raise PolicyError("deployed release metadata has an unexpected Pages base")
    contract_id = metadata["contract_id"]
    contract_revision = metadata["contract_revision"]
    if not isinstance(contract_id, str) or not contract_id:
        raise PolicyError("deployed release contract identifier must be nonempty")
    if (
        not isinstance(contract_revision, int)
        or isinstance(contract_revision, bool)
        or contract_revision < 1
    ):
        raise PolicyError("deployed release contract revision must be a positive integer")
    return version


def plan_deployment(candidate_text: str, deployed_metadata: object | None) -> DeploymentPlan:
    """Apply stable-only, strictly monotonic Pages promotion policy."""
    candidate = parse_version(candidate_text)
    if candidate.prerelease:
        return DeploymentPlan(candidate_text, None, False, "prereleases are publish-only")
    if deployed_metadata is None:
        return DeploymentPlan(candidate_text, None, True, "no existing Pages release was found")
    deployed_text = validate_deployed_metadata(deployed_metadata)
    deployed = parse_version(deployed_text)
    if compare_versions(candidate, deployed) > 0:
        return DeploymentPlan(
            candidate_text,
            deployed_text,
            True,
            f"{candidate_text} is newer than deployed {deployed_text}",
        )
    return DeploymentPlan(
        candidate_text,
        deployed_text,
        False,
        f"{candidate_text} does not supersede deployed {deployed_text}",
    )


def fetch_deployed_metadata(url: str) -> object | None:
    """Fetch uncached Pages metadata; a 404 means no release has been deployed."""
    parsed = urllib.parse.urlsplit(url)
    query = urllib.parse.parse_qsl(parsed.query, keep_blank_values=True)
    query.append(("foilbench_release_check", str(time.time_ns())))
    uncached_url = urllib.parse.urlunsplit(
        (parsed.scheme, parsed.netloc, parsed.path, urllib.parse.urlencode(query), parsed.fragment)
    )
    request = urllib.request.Request(
        uncached_url,
        headers={"Accept": "application/json", "Cache-Control": "no-cache"},
    )
    try:
        with urllib.request.urlopen(request, timeout=20) as response:
            payload = response.read(_MAX_METADATA_BYTES + 1)
    except urllib.error.HTTPError as error:
        if error.code == 404:
            return None
        raise PolicyError(f"deployed metadata request returned HTTP {error.code}") from error
    except urllib.error.URLError as error:
        raise PolicyError(f"could not fetch deployed metadata: {error.reason}") from error
    if len(payload) > _MAX_METADATA_BYTES:
        raise PolicyError("deployed metadata exceeds the size limit")
    try:
        return json.loads(payload)
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise PolicyError("deployed metadata is not valid UTF-8 JSON") from error


def _write_github_output(path: Path, plan: DeploymentPlan) -> None:
    values = {
        "candidate": plan.candidate,
        "deployed_version": plan.deployed_version or "",
        "deploy": str(plan.deploy).lower(),
        "reason": plan.reason,
    }
    with path.open("a", encoding="utf-8", newline="\n") as output:
        for key, value in values.items():
            output.write(f"{key}={value}\n")


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--candidate", required=True)
    source = parser.add_mutually_exclusive_group()
    source.add_argument("--metadata-url")
    source.add_argument("--metadata-file", type=Path)
    parser.add_argument("--github-output", type=Path)
    return parser.parse_args()


def main() -> int:
    args = _parse_args()
    try:
        candidate = cast(str, args.candidate)
        parsed_candidate = parse_version(candidate)
        metadata: object | None = None
        if not parsed_candidate.prerelease:
            metadata_url = cast(str | None, args.metadata_url)
            metadata_file = cast(Path | None, args.metadata_file)
            if metadata_url is not None:
                metadata = fetch_deployed_metadata(metadata_url)
            elif metadata_file is not None:
                metadata = json.loads(metadata_file.read_text(encoding="utf-8"))
        plan = plan_deployment(candidate, metadata)
        github_output = cast(Path | None, args.github_output)
        if github_output is not None:
            _write_github_output(github_output, plan)
        print(json.dumps(asdict(plan), separators=(",", ":")))
    except (OSError, json.JSONDecodeError, PolicyError) as error:
        print(f"release deployment policy error: {error}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
