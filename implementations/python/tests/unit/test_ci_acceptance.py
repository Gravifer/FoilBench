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
    for implementation, target, gate in module.CELLS:
        directory = root / implementation / target / gate
        directory.mkdir(parents=True)
        log = b"gate passed\n"
        evidence = b'{"completed":true}\n'
        (directory / "gate.log").write_bytes(log)
        (directory / "evidence.json").write_bytes(evidence)
        cell = {
            "schema_version": 2,
            "contract_id": "foilbench-phase3-v1",
            "contract_revision": 5,
            "commit": commit,
            "configuration_digest": digest,
            "implementation": implementation,
            "execution_target": target,
            "gate": gate,
            "case": "default-160x96",
            "thresholds": {"exit_code_zero": True},
            "measurements": {
                "evidence_sha256": hashlib.sha256(evidence).hexdigest()
            },
            "status": "passed",
            "log_file": "gate.log",
            "log_sha256": hashlib.sha256(log).hexdigest(),
        }
        (directory / "cell.json").write_text(json.dumps(cell), encoding="utf-8")


def test_acceptance_aggregator_requires_exact_distinct_cells(tmp_path: Path) -> None:
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

    missing = tmp_path / "typescript" / "browser-worker" / "preview" / "cell.json"
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
    (tmp_path / "python" / "native" / "startup" / "gate.log").write_text(
        "changed", encoding="utf-8"
    )
    with pytest.raises(ValueError, match="log digest mismatch"):
        module.validate_cells(tmp_path, commit, repository)
