"""Human-readable comparison of schema-compatible benchmark results."""

import json
from collections.abc import Sequence
from pathlib import Path
from typing import cast

from foilbench_py.benchmark.artifact import (
    physical_identities_match,
    physical_identity,
    validate_result_semantics,
)
from foilbench_py.benchmark.runner import load_matrix
from foilbench_py.core._schema_adapter import validate_json
from foilbench_py.core.scenario import find_repo_root


def collect_results(directory: str | Path) -> list[dict[str, object]]:
    results: list[dict[str, object]] = []
    root = find_repo_root(Path(__file__))
    schemas: dict[int, dict[str, object]] = {}
    for version, path in (
        (1, root / "spec" / "schemas" / "result.schema.json"),
        (2, root / "spec" / "proposals" / "revision5" / "schemas" / "result-v2.schema.json"),
    ):
        schema_value = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(schema_value, dict):
            raise TypeError("result schema must contain an object")
        schemas[version] = cast(dict[str, object], schema_value)
    for path in sorted(Path(directory).rglob("*.json")):
        value = json.loads(path.read_text(encoding="utf-8"))
        if isinstance(value, dict):
            typed = cast(dict[str, object], value)
            if (
                typed.get("schema_version") in schemas
                and "solver" in typed
                and "benchmark_matrix_id" in typed
                and "success" in typed
            ):
                validate_json(typed, schemas[int(cast(int, typed["schema_version"]))])
                validate_result_semantics(typed)
                results.append(typed)
    return results


def _assert_matched_identities(results: list[dict[str, object]]) -> None:
    signatures: dict[tuple[str, str, str, str, str], dict[str, object]] = {}
    for result in results:
        resolution = json.dumps(result["resolution"], separators=(",", ":"))
        key = (
            str(result["benchmark_matrix_id"]),
            str(result["scenario_id"]),
            str(result["precision"]),
            resolution,
            str(result["solver"]),
        )
        signature = physical_identity(result)
        previous = signatures.setdefault(key, signature)
        if not physical_identities_match(
            previous, signature, precision=str(result["precision"])
        ):
            raise ValueError(
                "benchmark artifacts reuse a matrix/scenario/resolution identity "
                "with different physical inputs"
            )


def _assert_required_languages(
    results: list[dict[str, object]], required_languages: Sequence[str]
) -> None:
    expected = set(required_languages)
    if not expected or len(expected) != len(required_languages):
        raise ValueError("required languages must be a non-empty unique roster")
    observed = {str(result["language"]) for result in results}
    if observed != expected:
        raise ValueError(
            "benchmark producer roster mismatch: "
            f"missing={sorted(expected - observed)!r} extra={sorted(observed - expected)!r}"
        )


def _producer(result: dict[str, object]) -> str:
    implementation = str(result.get("implementation", result["language"]))
    if "execution_target" in result:
        target = str(result["execution_target"])
    elif implementation == "typescript":
        target = "browser-worker"
    else:
        target = "native"
    return f"{implementation}/{target}"


def _assert_required_producers(
    results: list[dict[str, object]], required_producers: Sequence[str]
) -> None:
    expected = set(required_producers)
    if not expected or len(expected) != len(required_producers):
        raise ValueError("required producers must be a non-empty unique roster")
    observed = {_producer(result) for result in results}
    if observed != expected:
        raise ValueError(
            "benchmark producer/target roster mismatch: "
            f"missing={sorted(expected - observed)!r} extra={sorted(observed - expected)!r}"
        )


def _assert_complete_matrices(
    results: list[dict[str, object]], required_languages: Sequence[str] = (),
    required_producers: Sequence[str] = (),
) -> None:
    root = find_repo_root(Path(__file__))
    matrix_paths: dict[str, Path] = {}
    for path in (root / "benchmark-matrices").glob("*.json"):
        document = cast(object, json.loads(path.read_text(encoding="utf-8")))
        if isinstance(document, dict):
            typed_document = cast(dict[str, object], document)
            if isinstance(typed_document.get("id"), str):
                matrix_paths[str(typed_document["id"])] = path
    grouped: dict[tuple[str, str], list[dict[str, object]]] = {}
    for result in results:
        grouped.setdefault(
            (
                str(result["benchmark_matrix_id"]),
                _producer(result) if required_producers else str(result["language"]),
            ), []
        ).append(result)
    matrix_ids = {str(result["benchmark_matrix_id"]) for result in results}
    producers = (
        set(required_producers)
        if required_producers
        else set(required_languages)
        if required_languages
        else {str(result["language"]) for result in results}
    )
    for matrix_id in matrix_ids:
        for producer in producers:
            selected = grouped.get((matrix_id, producer), [])
            path = matrix_paths.get(matrix_id)
            if path is None:
                raise ValueError(f"cannot verify completeness of unknown matrix {matrix_id}")
            matrix = load_matrix(path)
            expected = {
                (solver, resolution, repetition)
                for solver in matrix.solvers
                for resolution in matrix.resolutions
                for repetition in range(1, matrix.repetitions + 1)
            }
            observed = [
                (
                    str(result["solver"]),
                    tuple(cast(list[int], result["resolution"])),
                    int(cast(int, result["repetition"])),
                )
                for result in selected
            ]
            if len(observed) != len(set(observed)):
                raise ValueError(f"duplicate {producer} artifacts for matrix {matrix_id}")
            missing = expected - set(observed)
            extra = set(observed) - expected
            failed = [item for item in selected if item["success"] is not True]
            if missing or extra or failed:
                raise ValueError(
                    f"incomplete {producer} artifacts for matrix {matrix_id}: "
                    f"missing={sorted(missing)!r} extra={sorted(extra)!r} "
                    f"failed={len(failed)}"
                )


def format_comparison(
    directory: str | Path,
    *,
    require_complete: bool = False,
    required_languages: Sequence[str] = (),
    required_producers: Sequence[str] = (),
) -> str:
    results = collect_results(directory)
    if not results:
        if require_complete or required_languages or required_producers:
            raise ValueError("strict benchmark comparison found no result artifacts")
        return "No benchmark result JSON files found."
    _assert_matched_identities(results)
    if required_languages:
        _assert_required_languages(results, required_languages)
    if required_producers:
        _assert_required_producers(results, required_producers)
    if require_complete:
        _assert_complete_matrices(results, required_languages, required_producers)
    header = (
        f"{'language':<12} {'solver':<18} {'median ms':>11} "
        f"{'p95 ms':>11} {'sim/wall':>11} {'success':>8}"
    )
    lines = [header, "-" * len(header)]
    for result in results:
        lines.append(
            f"{result['language']!s:<12} "
            f"{result['solver']!s:<18} "
            f"{1000.0 * float(cast(float, result['median_step_seconds'])):>11.3f} "
            f"{1000.0 * float(cast(float, result['p95_step_seconds'])):>11.3f} "
            f"{float(cast(float, result['simulated_seconds_per_wall_second'])):>11.3f} "
            f"{result['success']!s:>8}"
        )
    return "\n".join(lines)
