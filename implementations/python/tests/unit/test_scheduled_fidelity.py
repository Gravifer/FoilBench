from __future__ import annotations

import importlib.util
import json
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
    repository = Path(__file__).resolve().parents[4]
    matrix = cast(
        dict[str, object],
        json.loads(
            (repository / "benchmark-matrices/fidelity-recovery.json").read_text(
                encoding="utf-8"
            )
        ),
    )
    scenario = cast(
        dict[str, object],
        json.loads(
            (repository / cast(str, matrix["scenario"])).read_text(encoding="utf-8")
        ),
    )
    producers = [
        ("python", "native"),
        ("julia", "native"),
        ("typescript", "browser-worker"),
        ("rust", "native"),
    ]
    return [
        {
            "benchmark_matrix_id": "fidelity-recovery",
            "schema_version": 2,
            "contract_id": "foilbench-phase3-v1",
            "contract_revision": 5,
            "scenario_id": scenario["id"],
            "repetition": 1,
            "language": implementation,
            "implementation": implementation,
            "execution_target": target,
            "solver": solver,
            "git_commit": commit,
            "success": True,
            "precision": scenario["precision"],
            "resolution": cast(list[object], matrix["resolutions"])[0],
            "bounds": scenario["bounds"],
            "periodic_axes": scenario["periodic_axes"],
            "reynolds": scenario["reynolds"],
            "effective_reynolds": scenario["reynolds"],
            "solver_configuration": {
                "initial_condition": "freestream",
                "stable_advection": "maccormack",
                "stable_face_advection": False,
                "stable_cfl": 1.25,
                "pressure_tolerance": 0.001,
                "pressure_max_iterations": 640,
                "mac_maximum_divergence_linf": 2.25,
                "mac_maximum_solid_leakage": 0.0001,
                "pic_flip_blend": 0.95,
                "pic_population_interval": 8,
                "pic_cfl": 1.0,
            },
            "freestream": scenario["freestream"],
            "foil": scenario["foil"],
            "control_history": scenario["controls"],
            "requested_duration": matrix["duration"],
            "simulated_duration": matrix["duration"],
            "output_dt": scenario["output_dt"],
            "seed": scenario["seed"],
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


def test_scheduled_fidelity_validator_requires_the_censoring_limit() -> None:
    module = _module()
    documents = _documents("abc123")
    diagnostics = cast(dict[str, object], documents[0]["diagnostics"])
    diagnostics["recovery_elapsed"] = 0.0
    with pytest.raises(ValueError, match="censored recovery must report"):
        module.validate_documents(documents, "abc123")


def test_scheduled_fidelity_validator_accepts_float32_identity_roundoff() -> None:
    module = _module()
    documents = _documents("abc123")
    for document in documents:
        if document["implementation"] == "julia":
            document["output_dt"] = 0.01666666753590107
    summary = module.validate_documents(documents, "abc123")
    assert len(cast(list[object], summary["cells"])) == 12


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("scenario_id", "wrong"),
        ("simulated_duration", 0.0),
        ("precision", "float64"),
        ("output_dt", 99.0),
        ("control_history", [{"time": 0.0, "angle_degrees": 4.0}]),
        ("reynolds", 42.0),
        ("effective_reynolds", 999.0),
        ("solver_configuration", {"initial_condition": "freestream"}),
        ("seed", 7),
    ],
)
def test_scheduled_fidelity_validator_binds_the_declared_run(
    field: str, value: object
) -> None:
    module = _module()
    documents = _documents("abc123")
    documents[0][field] = value
    with pytest.raises(ValueError, match="scheduled-fidelity"):
        module.validate_documents(documents, "abc123")


def test_scheduled_fidelity_validator_accepts_a_matched_lbm_clamp() -> None:
    module = _module()
    documents = _documents("abc123")
    for document in documents:
        if document["solver"] == "lbm-d2q9":
            document["effective_reynolds"] = 750.0
    summary = module.validate_documents(documents, "abc123")
    assert len(cast(list[object], summary["cells"])) == 12


def test_scheduled_fidelity_validator_rejects_a_mismatched_lbm_clamp() -> None:
    module = _module()
    documents = _documents("abc123")
    for document in documents:
        if document["solver"] == "lbm-d2q9":
            document["effective_reynolds"] = 750.0
    documents[1]["effective_reynolds"] = 749.0
    with pytest.raises(ValueError, match=r"inconsistent.*effective_reynolds"):
        module.validate_documents(documents, "abc123")
