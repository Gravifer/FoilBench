"""Validate the exact Revision 5 scheduled mixing/recovery evidence roster."""

from __future__ import annotations

import argparse
import json
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


def _finite_number(value: object, name: str) -> float:
    if not isinstance(value, (int, float)) or isinstance(value, bool):
        raise TypeError(f"{name} must be numeric")
    result = float(value)
    if not (-float("inf") < result < float("inf")):
        raise ValueError(f"{name} must be finite")
    return result


def validate_documents(
    documents: list[dict[str, object]], expected_commit: str
) -> dict[str, object]:
    cells: dict[tuple[str, str, str], dict[str, object]] = {}
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
        if document.get("success") is not True:
            raise ValueError(f"failed scheduled-fidelity cell: {producer}/{solver}")
        if document.get("resolution") != [32, 20]:
            raise ValueError(f"wrong scheduled-fidelity resolution: {producer}/{solver}")
        if _finite_number(document.get("requested_duration"), "requested_duration") != 22.0:
            raise ValueError(f"wrong scheduled-fidelity duration: {producer}/{solver}")
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
