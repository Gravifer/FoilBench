import hashlib
import importlib.util
import json
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
    hashes = [
        hashlib.sha256((repository / path).read_bytes()).hexdigest()
        for path in module.CONFIGURATION
    ]
    digest = hashlib.sha256("\n".join(hashes).encode()).hexdigest()
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
    _roster(tmp_path, repository, "abc123", module)
    module.validate_cells(tmp_path, "abc123", repository)

    missing = tmp_path / "typescript" / "preview" / "cell.json"
    missing.unlink()
    with pytest.raises(ValueError, match="roster mismatch"):
        module.validate_cells(tmp_path, "abc123", repository)


def test_acceptance_aggregator_rejects_log_tampering(tmp_path: Path) -> None:
    repository = Path(__file__).resolve().parents[4]
    module = _module(repository)
    _roster(tmp_path, repository, "abc123", module)
    (tmp_path / "python" / "startup" / "gate.log").write_text("changed", encoding="utf-8")
    with pytest.raises(ValueError, match="log digest mismatch"):
        module.validate_cells(tmp_path, "abc123", repository)
