"""Validate the exact Revision 5 scheduled mixing/recovery evidence roster."""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import subprocess
from pathlib import Path
from typing import cast

from jsonschema import Draft202012Validator

MATRIX_ID = "fidelity-recovery"
EXPECTED_PRODUCERS = {
    ("python", "native"),
    ("julia", "native"),
    ("typescript", "browser-worker"),
    ("rust", "native"),
}
EXPECTED_SOLVERS = {"stable-fluids", "lbm-d2q9", "pic-flip"}

_SOLVER_OPTION_DEFAULTS: dict[str, object] = {
    "initial_condition": "freestream",
    "stable_advection": "maccormack",
    "stable_face_advection": False,
    "stable_cfl": 0.7,
    "pressure_tolerance": 1.0e-5,
    "pressure_max_iterations": 640,
    "mac_maximum_divergence_linf": None,
    "mac_maximum_solid_leakage": None,
    "pic_flip_blend": 0.95,
    "pic_population_interval": 8,
    "pic_cfl": 0.75,
}


def _semantically_equal(actual: object, expected: object, precision: str) -> bool:
    if (
        isinstance(actual, (int, float))
        and not isinstance(actual, bool)
        and isinstance(expected, (int, float))
        and not isinstance(expected, bool)
    ):
        tolerance = 1.0e-6 if precision == "float32" else 1.0e-12
        return math.isclose(float(actual), float(expected), rel_tol=tolerance, abs_tol=tolerance)
    if isinstance(actual, list) and isinstance(expected, list):
        return len(actual) == len(expected) and all(
            _semantically_equal(left, right, precision)
            for left, right in zip(actual, expected, strict=True)
        )
    if isinstance(actual, dict) and isinstance(expected, dict):
        return actual.keys() == expected.keys() and all(
            _semantically_equal(actual[key], expected[key], precision)
            for key in actual
        )
    return actual == expected


def _expected_solver_configuration(scenario: dict[str, object]) -> dict[str, object]:
    options = scenario.get("solver_options", {})
    if not isinstance(options, dict):
        raise TypeError("scheduled-fidelity scenario has invalid solver options")
    return {
        name: options.get(name, default)
        for name, default in _SOLVER_OPTION_DEFAULTS.items()
        if default is not None or name in options
    }


def _expected_configuration(repository: Path) -> tuple[dict[str, object], str]:
    matrix = cast(
        dict[str, object],
        json.loads(
            (repository / "benchmark-matrices/fidelity-recovery.json").read_text(
                encoding="utf-8"
            )
        ),
    )
    scenario_path = matrix.get("scenario")
    if not isinstance(scenario_path, str):
        raise TypeError("scheduled-fidelity matrix omits its scenario")
    scenario = cast(
        dict[str, object],
        json.loads((repository / scenario_path).read_text(encoding="utf-8")),
    )
    controls = scenario.get("controls")
    if not isinstance(controls, list):
        raise TypeError("scheduled-fidelity scenario omits its control history")
    expected = {
        "schema_version": 2,
        "contract_id": "foilbench-phase3-v1",
        "contract_revision": 5,
        "benchmark_matrix_id": matrix.get("id"),
        "scenario_id": scenario.get("id"),
        "repetition": 1,
        "precision": scenario.get("precision"),
        "resolution": cast(list[object], matrix.get("resolutions"))[0],
        "bounds": scenario.get("bounds"),
        "periodic_axes": scenario.get("periodic_axes"),
        "reynolds": scenario.get("reynolds"),
        "freestream": scenario.get("freestream"),
        "foil": scenario.get("foil"),
        "control_history": controls,
        "solver_configuration": _expected_solver_configuration(scenario),
        "requested_duration": matrix.get("duration"),
        "output_dt": scenario.get("output_dt"),
        "seed": scenario.get("seed"),
    }
    digest_payload = json.dumps(
        {"matrix": matrix, "scenario": scenario},
        sort_keys=True,
        separators=(",", ":"),
    ).encode()
    return expected, hashlib.sha256(digest_payload).hexdigest()


def _finite_number(value: object, name: str) -> float:
    if not isinstance(value, (int, float)) or isinstance(value, bool):
        raise TypeError(f"{name} must be numeric")
    result = float(value)
    if not (-float("inf") < result < float("inf")):
        raise ValueError(f"{name} must be finite")
    return result


def validate_documents(
    documents: list[dict[str, object]],
    expected_commit: str,
    repository: Path | None = None,
) -> dict[str, object]:
    selected_repository = repository or Path(__file__).resolve().parents[1]
    expected_identity, configuration_digest = _expected_configuration(
        selected_repository
    )
    cells: dict[tuple[str, str, str], dict[str, object]] = {}
    effective_reynolds_by_solver: dict[str, float] = {}
    for document in documents:
        if document.get("benchmark_matrix_id") != MATRIX_ID:
            continue
        implementation = str(document.get("implementation", ""))
        target = str(document.get("execution_target", ""))
        solver = str(document.get("solver", ""))
        producer = (implementation, target)
        if producer not in EXPECTED_PRODUCERS or solver not in EXPECTED_SOLVERS:
            raise ValueError(f"unexpected scheduled-fidelity cell: {producer}/{solver}")
        if document.get("git_commit") != expected_commit:
            raise ValueError(f"stale scheduled-fidelity cell: {producer}/{solver}")
        if document.get("language") != implementation:
            raise ValueError(f"inconsistent language identity: {producer}/{solver}")
        precision = str(expected_identity["precision"])
        for field, expected_value in expected_identity.items():
            if not _semantically_equal(document.get(field), expected_value, precision):
                raise ValueError(
                    f"wrong scheduled-fidelity {field}: {producer}/{solver}"
                )
        requested_reynolds = _finite_number(
            expected_identity["reynolds"], "requested reynolds"
        )
        effective_reynolds = _finite_number(
            document.get("effective_reynolds"), "effective_reynolds"
        )
        if effective_reynolds <= 0.0:
            raise ValueError(
                f"invalid scheduled-fidelity effective_reynolds: {producer}/{solver}"
            )
        if solver in {"stable-fluids", "pic-flip"} and not _semantically_equal(
            effective_reynolds, requested_reynolds, precision
        ):
            raise ValueError(
                f"wrong scheduled-fidelity effective_reynolds: {producer}/{solver}"
            )
        if solver == "lbm-d2q9" and effective_reynolds > requested_reynolds and not (
            _semantically_equal(effective_reynolds, requested_reynolds, precision)
        ):
            raise ValueError(
                f"invalid scheduled-fidelity effective_reynolds: {producer}/{solver}"
            )
        reference_reynolds = effective_reynolds_by_solver.setdefault(
            solver, effective_reynolds
        )
        if not _semantically_equal(effective_reynolds, reference_reynolds, precision):
            raise ValueError(
                "inconsistent scheduled-fidelity effective_reynolds: "
                f"{producer}/{solver}"
            )
        if document.get("success") is not True:
            raise ValueError(f"failed scheduled-fidelity cell: {producer}/{solver}")
        simulated_duration = _finite_number(
            document.get("simulated_duration"), "simulated_duration"
        )
        if abs(simulated_duration - 22.0) > 1.0e-6:
            raise ValueError(
                f"incomplete scheduled-fidelity duration: {producer}/{solver}"
            )
        diagnostics = document.get("diagnostics")
        if not isinstance(diagnostics, dict):
            raise TypeError(f"missing scheduled diagnostics: {producer}/{solver}")
        mixing = _finite_number(diagnostics.get("wake_mixing_index"), "wake_mixing_index")
        observed = _finite_number(diagnostics.get("recovery_observed"), "recovery_observed")
        elapsed = _finite_number(diagnostics.get("recovery_elapsed"), "recovery_elapsed")
        baseline = _finite_number(diagnostics.get("recovery_baseline_time"), "recovery_baseline_time")
        recovery_start = _finite_number(diagnostics.get("recovery_start_time"), "recovery_start_time")
        if mixing < 0.0 or observed not in (0.0, 1.0) or not 0.0 <= elapsed <= 4.0 + 1.0e-9:
            raise ValueError(f"invalid scheduled measurements: {producer}/{solver}")
        if observed == 0.0 and abs(elapsed - 4.0) > 1.0e-9:
            raise ValueError(
                f"censored recovery must report the observation limit: {producer}/{solver}"
            )
        if abs(baseline - 3.0) > 1.0e-9 or abs(recovery_start - 18.0) > 1.0e-9:
            raise ValueError(f"wrong recovery window: {producer}/{solver}")
        warnings = document.get("warnings")
        if observed == 0.0 and (
            not isinstance(warnings, list)
            or not any("right-censored" in str(warning) for warning in warnings)
        ):
            raise ValueError(f"censored recovery is not disclosed: {producer}/{solver}")
        key = (implementation, target, solver)
        if key in cells:
            raise ValueError(f"duplicate scheduled-fidelity cell: {key}")
        cells[key] = {
            "implementation": implementation,
            "execution_target": target,
            "solver": solver,
            "wake_mixing_index": mixing,
            "recovery_observed": int(observed),
            "recovery_elapsed": elapsed,
        }

    expected = {
        (implementation, target, solver)
        for implementation, target in EXPECTED_PRODUCERS
        for solver in EXPECTED_SOLVERS
    }
    missing = expected - cells.keys()
    if missing:
        raise ValueError(f"missing scheduled-fidelity cells: {sorted(missing)}")
    return {
        "schema_version": 1,
        "matrix": MATRIX_ID,
        "commit": expected_commit,
        "configuration_digest": configuration_digest,
        "cells": [cells[key] for key in sorted(cells)],
    }


def _documents(root: Path) -> list[dict[str, object]]:
    output: list[dict[str, object]] = []
    for path in root.rglob("*.json"):
        value = json.loads(path.read_text(encoding="utf-8"))
        if isinstance(value, dict) and value.get("benchmark_matrix_id") == MATRIX_ID:
            output.append(cast(dict[str, object], value))
    return output


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("results", type=Path)
    parser.add_argument("--commit")
    parser.add_argument("--summary", type=Path)
    arguments = parser.parse_args()
    repository = Path(__file__).resolve().parents[1]
    expected_commit = arguments.commit or subprocess.run(
        ["git", "-C", str(repository), "rev-parse", "HEAD"],
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()
    documents = _documents(arguments.results)
    schema = json.loads(
        (repository / "spec/schemas/result-v2.schema.json").read_text(encoding="utf-8")
    )
    validator = Draft202012Validator(schema)
    for document in documents:
        validator.validate(document)
    summary = validate_documents(documents, expected_commit)
    text = json.dumps(summary, indent=2) + "\n"
    if arguments.summary is not None:
        arguments.summary.parent.mkdir(parents=True, exist_ok=True)
        arguments.summary.write_text(text, encoding="utf-8")
    print(text, end="")


if __name__ == "__main__":
    main()
