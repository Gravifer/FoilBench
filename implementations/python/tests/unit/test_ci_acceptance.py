import hashlib
import importlib.util
import json
import subprocess
from pathlib import Path
from types import ModuleType

import pytest


def _module(root: Path) -> ModuleType:
    path = root / "tools" / "ci" / "validate_acceptance_cells.py"
    specification = importlib.util.spec_from_file_location("acceptance_cells", path)
    assert specification is not None and specification.loader is not None
    module = importlib.util.module_from_spec(specification)
    specification.loader.exec_module(module)
    return module


def _roster(root: Path, repository: Path, commit: str, module: ModuleType) -> None:
    digest = module.configuration_digest(repository, commit)
    for (producer, gate), target in module.TARGETS.items():
        directory = root / producer / gate
        directory.mkdir(parents=True)
        log = b"gate passed\n"
        (directory / "gate.log").write_bytes(log)
        cell = {
            "schema_version": 1,
            "commit": commit,
            "configuration_digest": digest,
            "producer": producer,
            "execution_target": target,
            "gate": gate,
            "status": "passed",
            "log_file": "gate.log",
            "log_sha256": hashlib.sha256(log).hexdigest(),
        }
        (directory / "cell.json").write_text(json.dumps(cell), encoding="utf-8")


def test_acceptance_aggregator_requires_twelve_distinct_cells(tmp_path: Path) -> None:
    repository = Path(__file__).resolve().parents[4]
    module = _module(repository)
    commit = subprocess.run(
        ["git", "-C", str(repository), "rev-parse", "HEAD"],
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()
    _roster(tmp_path, repository, commit, module)
    module.validate_cells(tmp_path, commit, repository)

    missing = tmp_path / "typescript" / "preview" / "cell.json"
    missing.unlink()
    with pytest.raises(ValueError, match="roster mismatch"):
        module.validate_cells(tmp_path, commit, repository)


def test_acceptance_aggregator_rejects_log_tampering(tmp_path: Path) -> None:
    repository = Path(__file__).resolve().parents[4]
    module = _module(repository)
    commit = subprocess.run(
        ["git", "-C", str(repository), "rev-parse", "HEAD"],
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()
    _roster(tmp_path, repository, commit, module)
    (tmp_path / "python" / "startup" / "gate.log").write_text("changed", encoding="utf-8")
    with pytest.raises(ValueError, match="log digest mismatch"):
        module.validate_cells(tmp_path, commit, repository)
