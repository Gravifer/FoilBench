import json
import subprocess
import sys
from pathlib import Path

import pytest


def _git(repository: Path, *arguments: str) -> None:
    subprocess.run(
        [
            "git",
            "-c",
            "commit.gpgSign=false",
            "-c",
            "tag.gpgSign=false",
            "-C",
            str(repository),
            *arguments,
        ],
        check=True,
        capture_output=True,
        text=True,
    )


@pytest.fixture
def git_repository(tmp_path: Path) -> Path:
    repository = tmp_path / "repository"
    repository.mkdir()
    _git(repository, "init", "--quiet")
    _git(repository, "config", "user.email", "foilbench@example.invalid")
    _git(repository, "config", "user.name", "FoilBench tests")
    _git(repository, "commit", "--allow-empty", "--message", "baseline", "--quiet")
    return repository


def _run_candidate(
    repository: Path,
    candidate: str,
    *,
    require_absent: bool = False,
) -> subprocess.CompletedProcess[str]:
    script = Path(__file__).resolve().parents[4] / "tools" / "validate_web_release_candidate.py"
    arguments = [
        sys.executable,
        str(script),
        "--candidate",
        candidate,
        "--repository",
        str(repository),
    ]
    if require_absent:
        arguments.append("--require-candidate-tag-absent")
    return subprocess.run(arguments, check=False, capture_output=True, text=True)


def test_open_prerelease_series_is_allowed(git_repository: Path) -> None:
    result = _run_candidate(git_repository, "v0.2.0-rc.1", require_absent=True)

    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout) == {
        "candidate": "v0.2.0-rc.1",
        "prerelease": True,
        "stable_counterpart": "v0.2.0",
    }


def test_stable_release_closes_its_prerelease_series(git_repository: Path) -> None:
    _git(git_repository, "tag", "v0.1.0")

    result = _run_candidate(git_repository, "v0.1.0-rc.1", require_absent=True)

    assert result.returncode == 1
    assert "stable release v0.1.0 already closes prerelease series" in result.stderr


def test_new_stable_backport_is_allowed(git_repository: Path) -> None:
    _git(git_repository, "tag", "v4.7.1")

    result = _run_candidate(git_repository, "v4.2.13", require_absent=True)

    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout)["prerelease"] is False


def test_manual_release_rejects_an_existing_candidate_tag(git_repository: Path) -> None:
    _git(git_repository, "tag", "v0.2.0-rc.1")

    result = _run_candidate(git_repository, "v0.2.0-rc.1", require_absent=True)

    assert result.returncode == 1
    assert "release tag already exists" in result.stderr


def test_tag_trigger_accepts_its_own_candidate_tag(git_repository: Path) -> None:
    _git(git_repository, "tag", "v0.2.0-rc.1")

    result = _run_candidate(git_repository, "v0.2.0-rc.1")

    assert result.returncode == 0, result.stderr


def test_invalid_candidate_is_rejected(git_repository: Path) -> None:
    result = _run_candidate(git_repository, "0.2.0", require_absent=True)

    assert result.returncode == 1
    assert "invalid release version" in result.stderr
