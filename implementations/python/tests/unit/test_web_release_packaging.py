import json
import subprocess
import sys
from pathlib import Path
from zipfile import ZipFile

import pytest


def _write_metadata(
    directory: Path,
    *,
    commit: str = "0123456789abcdef0123456789abcdef01234567",
    built_at: str = "2026-08-23T12:34:56Z",
) -> None:
    metadata = {
        "release": True,
        "version": "v0.2.0",
        "commit": commit,
        "built_at": built_at,
    }
    (directory / "release.json").write_text(json.dumps(metadata), encoding="utf-8")


def _run_packager(distribution: Path, output: Path) -> subprocess.CompletedProcess[str]:
    script = Path(__file__).resolve().parents[4] / "tools" / "package_web_release.py"
    return subprocess.run(
        [
            sys.executable,
            str(script),
            "--dist",
            str(distribution),
            "--output",
            str(output),
            "--version",
            "v0.2.0",
        ],
        check=False,
        capture_output=True,
        text=True,
    )


def test_release_packager_accepts_canonical_identity(tmp_path: Path) -> None:
    distribution = tmp_path / "dist"
    distribution.mkdir()
    _write_metadata(distribution)
    (distribution / "index.html").write_text("FoilBench", encoding="utf-8")
    result = _run_packager(distribution, tmp_path / "release")
    assert result.returncode == 0, result.stderr
    assert (tmp_path / "release" / "foilbench-web-v0.2.0.zip").is_file()
    assert (tmp_path / "release" / "SHA256SUMS").is_file()


@pytest.mark.parametrize(
    ("built_at", "expected_year"),
    [
        ("1979-12-31T23:59:58Z", 1980),
        ("2107-12-31T23:59:58Z", 2107),
        ("2108-01-01T00:00:00Z", 2107),
        ("9999-01-01T00:00:00Z", 2107),
    ],
)
def test_release_packager_clamps_zip_timestamp_year(
    tmp_path: Path,
    built_at: str,
    expected_year: int,
) -> None:
    distribution = tmp_path / "dist"
    distribution.mkdir()
    _write_metadata(distribution, built_at=built_at)
    (distribution / "index.html").write_text("FoilBench", encoding="utf-8")
    output = tmp_path / "release"
    result = _run_packager(distribution, output)
    assert result.returncode == 0, result.stderr
    with ZipFile(output / "foilbench-web-v0.2.0.zip") as archive:
        assert {entry.date_time[0] for entry in archive.infolist()} == {expected_year}


@pytest.mark.parametrize(
    "commit",
    [
        "g" * 40,
        "A" * 40,
        "deadbeef",
    ],
)
def test_release_packager_rejects_noncanonical_commit(tmp_path: Path, commit: str) -> None:
    distribution = tmp_path / "dist"
    distribution.mkdir()
    _write_metadata(distribution, commit=commit)
    result = _run_packager(distribution, tmp_path / "release")
    assert result.returncode != 0
    assert "full commit identity" in result.stderr


@pytest.mark.parametrize(
    "built_at",
    [
        "2026-08-23",
        "2026-08-23T12:34:56",
        "2026-08-23T12:34:56+00:00",
        "2026-02-31T12:34:56Z",
        "soon",
    ],
)
def test_release_packager_rejects_noncanonical_build_time(
    tmp_path: Path,
    built_at: str,
) -> None:
    distribution = tmp_path / "dist"
    distribution.mkdir()
    _write_metadata(distribution, built_at=built_at)
    result = _run_packager(distribution, tmp_path / "release")
    assert result.returncode != 0
    assert "UTC ISO-8601" in result.stderr
