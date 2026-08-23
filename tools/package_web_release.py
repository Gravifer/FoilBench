from __future__ import annotations

import argparse
import hashlib
import json
import re
import stat
import zipfile
from datetime import datetime
from pathlib import Path
from typing import Final, TypedDict, cast


class ReleaseMetadata(TypedDict):
    release: bool
    version: str | None
    commit: str | None
    built_at: str | None


_METADATA_NAME: Final = "release.json"
_RELEASE_VERSION: Final = re.compile(
    r"^v(0|[1-9]\d*)\.(0|[1-9]\d*)\.(0|[1-9]\d*)"
    r"(?:-(?:(?:0|[1-9]\d*)|(?:\d*[A-Za-z-][0-9A-Za-z-]*))"
    r"(?:\.(?:(?:0|[1-9]\d*)|(?:\d*[A-Za-z-][0-9A-Za-z-]*)))*)?$"
)
_RELEASE_COMMIT: Final = re.compile(r"^[0-9a-f]{40}$")
_RELEASE_BUILT_AT: Final = re.compile(
    r"^\d{4}-(?:0[1-9]|1[0-2])-(?:0[1-9]|[12]\d|3[01])"
    r"T(?:[01]\d|2[0-3]):[0-5]\d:[0-5]\dZ$"
)


def _parse_built_at(value: str) -> datetime:
    if _RELEASE_BUILT_AT.fullmatch(value) is None:
        raise ValueError("release.json build time must be a UTC ISO-8601 timestamp")
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as error:
        raise ValueError("release.json build time must be a UTC ISO-8601 timestamp") from error


def _read_metadata(distribution: Path) -> ReleaseMetadata:
    value = cast(object, json.loads((distribution / _METADATA_NAME).read_text(encoding="utf-8")))
    if not isinstance(value, dict):
        raise TypeError("release.json must contain an object")
    release = value.get("release")
    version = value.get("version")
    commit = value.get("commit")
    built_at = value.get("built_at")
    if release is not True or not isinstance(version, str):
        raise ValueError("distribution is not a release build")
    if _RELEASE_VERSION.fullmatch(version) is None:
        raise ValueError("release.json contains an invalid release version")
    if not isinstance(commit, str) or _RELEASE_COMMIT.fullmatch(commit) is None:
        raise ValueError("release.json does not contain a full commit identity")
    if not isinstance(built_at, str):
        raise TypeError("release.json does not contain a build time")
    _parse_built_at(built_at)
    return {"release": release, "version": version, "commit": commit, "built_at": built_at}


def _zip_timestamp(built_at: str) -> tuple[int, int, int, int, int, int]:
    parsed = _parse_built_at(built_at)
    year = min(max(parsed.year, 1980), 2107)
    return year, parsed.month, parsed.day, parsed.hour, parsed.minute, parsed.second


def _write_archive(distribution: Path, archive: Path, built_at: str) -> None:
    timestamp = _zip_timestamp(built_at)
    files = sorted(path for path in distribution.rglob("*") if path.is_file())
    if not files:
        raise ValueError("web distribution is empty")
    archive.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(archive, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=9) as bundle:
        for path in files:
            relative = path.relative_to(distribution).as_posix()
            info = zipfile.ZipInfo(relative, date_time=timestamp)
            info.compress_type = zipfile.ZIP_DEFLATED
            info.external_attr = (stat.S_IFREG | 0o644) << 16
            bundle.writestr(info, path.read_bytes(), compresslevel=9)


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def package_release(distribution: Path, output_directory: Path, expected_version: str) -> tuple[Path, Path]:
    distribution = distribution.resolve(strict=True)
    metadata = _read_metadata(distribution)
    if metadata["version"] != expected_version:
        raise ValueError(
            f"release version mismatch: expected {expected_version}, found {metadata['version']}"
        )
    archive = output_directory.resolve() / f"foilbench-web-{expected_version}.zip"
    _write_archive(distribution, archive, cast(str, metadata["built_at"]))
    checksum = output_directory.resolve() / "SHA256SUMS"
    checksum.write_text(f"{_sha256(archive)}  {archive.name}\n", encoding="ascii", newline="\n")
    return archive, checksum


def main() -> None:
    parser = argparse.ArgumentParser(description="Package an identified FoilBench web release")
    parser.add_argument("--dist", type=Path, default=Path("apps/web/dist"))
    parser.add_argument("--output", type=Path, default=Path("dist/release"))
    parser.add_argument("--version", required=True)
    arguments = parser.parse_args()
    archive, checksum = package_release(arguments.dist, arguments.output, arguments.version)
    print(archive)
    print(checksum)


if __name__ == "__main__":
    main()
