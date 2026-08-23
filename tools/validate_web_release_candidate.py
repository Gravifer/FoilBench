"""Validate whether a FoilBench version may enter the release pipeline."""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import cast

from plan_web_deployment import PolicyError, parse_version


@dataclass(frozen=True, slots=True)
class CandidateReport:
    candidate: str
    prerelease: bool
    stable_counterpart: str | None


def _tag_exists(repository: Path, tag: str) -> bool:
    result = subprocess.run(
        ["git", "-C", str(repository), "show-ref", "--verify", "--quiet", f"refs/tags/{tag}"],
        check=False,
        capture_output=True,
        text=True,
    )
    if result.returncode == 0:
        return True
    if result.returncode == 1:
        return False
    detail = result.stderr.strip() or f"git show-ref exited with {result.returncode}"
    raise PolicyError(f"could not inspect release tags: {detail}")


def validate_candidate(
    candidate_text: str,
    repository: Path,
    *,
    require_candidate_tag_absent: bool,
) -> CandidateReport:
    """Validate tag uniqueness and closure of a stable release series."""
    candidate = parse_version(candidate_text)
    if require_candidate_tag_absent and _tag_exists(repository, candidate_text):
        raise PolicyError(f"release tag already exists: {candidate_text}")
    if not candidate.prerelease:
        return CandidateReport(candidate_text, False, None)
    stable_counterpart = f"v{candidate.major}.{candidate.minor}.{candidate.patch}"
    if _tag_exists(repository, stable_counterpart):
        raise PolicyError(
            f"stable release {stable_counterpart} already closes prerelease series {candidate_text}"
        )
    return CandidateReport(candidate_text, True, stable_counterpart)


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--candidate", required=True)
    parser.add_argument("--repository", type=Path, default=Path("."))
    parser.add_argument("--require-candidate-tag-absent", action="store_true")
    return parser.parse_args()


def main() -> int:
    args = _parse_args()
    try:
        report = validate_candidate(
            cast(str, args.candidate),
            cast(Path, args.repository),
            require_candidate_tag_absent=cast(bool, args.require_candidate_tag_absent),
        )
        print(json.dumps(asdict(report), separators=(",", ":")))
    except PolicyError as error:
        print(f"release candidate policy error: {error}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
