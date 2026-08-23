import json
import subprocess
import sys
from pathlib import Path
from typing import Final

import pytest

_COMMIT: Final = "0123456789abcdef0123456789abcdef01234567"


def _metadata(version: str) -> dict[str, object]:
    return {
        "schema_version": 1,
        "release": True,
        "version": version,
        "commit": _COMMIT,
        "built_at": "2026-08-23T12:34:56Z",
        "pages_base": "/FoilBench/",
        "contract_id": "foilbench-phase3-v1",
        "contract_revision": 5,
    }


def _run_policy(
    candidate: str,
    temporary_directory: Path,
    deployed: object | None = None,
    *,
    github_output: Path | None = None,
) -> subprocess.CompletedProcess[str]:
    script = Path(__file__).resolve().parents[4] / "tools" / "plan_web_deployment.py"
    arguments = [sys.executable, str(script), "--candidate", candidate]
    if deployed is not None:
        metadata_path = temporary_directory / "release.json"
        metadata_path.write_text(json.dumps(deployed), encoding="utf-8")
        arguments.extend(("--metadata-file", str(metadata_path)))
    if github_output is not None:
        arguments.extend(("--github-output", str(github_output)))
    return subprocess.run(arguments, check=False, capture_output=True, text=True)


def test_first_stable_release_is_deployable(tmp_path: Path) -> None:
    result = _run_policy("v0.2.0", tmp_path)

    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout) == {
        "candidate": "v0.2.0",
        "deployed_version": None,
        "deploy": True,
        "reason": "no existing Pages release was found",
    }


def test_prerelease_is_publish_only_even_with_no_metadata_source(tmp_path: Path) -> None:
    result = _run_policy("v9.0.0-rc.1", tmp_path)

    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout)["deploy"] is False
    assert json.loads(result.stdout)["reason"] == "prereleases are publish-only"


@pytest.mark.parametrize(
    ("candidate", "deployed", "expected"),
    [
        ("v0.2.1", "v0.2.0", True),
        ("v1.0.0", "v0.99.99", True),
        ("v4.7.1", "v4.7.1", False),
        ("v4.2.13", "v4.7.1", False),
        ("v4.7.1", "v4.7.1-rc.10", True),
    ],
)
def test_stable_pages_promotion_is_strictly_monotonic(
    candidate: str,
    deployed: str,
    expected: bool,
    tmp_path: Path,
) -> None:
    result = _run_policy(candidate, tmp_path, _metadata(deployed))

    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout)["deploy"] is expected


@pytest.mark.parametrize(
    "metadata",
    [
        {**_metadata("v0.1.0"), "release": False},
        {**_metadata("v0.1.0"), "unexpected": True},
        {key: value for key, value in _metadata("v0.1.0").items() if key != "commit"},
        {**_metadata("not-semver")},
        {**_metadata("v0.1.0"), "pages_base": "/wrong/"},
    ],
)
def test_untrustworthy_live_metadata_fails_closed(metadata: object, tmp_path: Path) -> None:
    result = _run_policy("v0.2.0", tmp_path, metadata)

    assert result.returncode == 1
    assert "release deployment policy error" in result.stderr


def test_github_outputs_record_skip_reason(tmp_path: Path) -> None:
    output = tmp_path / "github-output.txt"

    result = _run_policy("v4.2.13", tmp_path, _metadata("v4.7.1"), github_output=output)

    assert result.returncode == 0, result.stderr
    assert output.read_text(encoding="utf-8").splitlines() == [
        "candidate=v4.2.13",
        "deployed_version=v4.7.1",
        "deploy=false",
        "reason=v4.2.13 does not supersede deployed v4.7.1",
    ]
