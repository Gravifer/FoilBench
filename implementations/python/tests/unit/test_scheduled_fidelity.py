from __future__ import annotations

import importlib.util
from pathlib import Path
from types import ModuleType
from typing import cast

import pytest


def _module() -> ModuleType:
    root = Path(__file__).resolve().parents[4]
    spec = importlib.util.spec_from_file_location(
        "validate_scheduled_fidelity", root / "tools/validate_scheduled_fidelity.py"
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _documents(commit: str) -> list[dict[str, object]]:
    producers = [
        ("python", "native"),
        ("julia", "native"),
        ("typescript", "browser-worker"),
        ("rust", "native"),
    ]
    return [
        {
            "benchmark_matrix_id": "fidelity-recovery",
            "implementation": implementation,
            "execution_target": target,
            "solver": solver,
            "git_commit": commit,
            "success": True,
            "resolution": [32, 20],
            "requested_duration": 22.0,
            "diagnostics": {
                "wake_mixing_index": 0.1,
                "recovery_baseline_time": 3.0,
                "recovery_start_time": 18.0,
                "recovery_observed": 0.0,
                "recovery_elapsed": 4.0,
            },
            "warnings": ["wake recovery was not observed; recovery_elapsed is right-censored"],
        }
        for implementation, target in producers
        for solver in ("stable-fluids", "lbm-d2q9", "pic-flip")
    ]


def test_scheduled_fidelity_validator_requires_the_exact_roster() -> None:
    module = _module()
    documents = _documents("abc123")
    summary = cast(dict[str, object], module.validate_documents(documents, "abc123"))
    assert len(cast(list[object], summary["cells"])) == 12

    with pytest.raises(ValueError, match="missing scheduled-fidelity cells"):
        module.validate_documents(documents[:-1], "abc123")


def test_scheduled_fidelity_validator_rejects_untruthful_recovery() -> None:
    module = _module()
    documents = _documents("abc123")
    diagnostics = cast(dict[str, object], documents[0]["diagnostics"])
    diagnostics["recovery_observed"] = 2.0
    with pytest.raises(ValueError, match="invalid scheduled measurements"):
        module.validate_documents(documents, "abc123")
