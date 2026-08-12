"""Human-readable comparison of schema-compatible benchmark results."""

import json
from pathlib import Path
from typing import cast

from foilbench_py.benchmark.artifact import (
    physical_identities_match,
    physical_identity,
    validate_result_semantics,
)
from foilbench_py.core._schema_adapter import validate_json
from foilbench_py.core.scenario import find_repo_root


def collect_results(directory: str | Path) -> list[dict[str, object]]:
    results: list[dict[str, object]] = []
    schema_path = find_repo_root(Path(__file__)) / "spec" / "result.schema.json"
    schema_value = json.loads(schema_path.read_text(encoding="utf-8"))
    if not isinstance(schema_value, dict):
        raise TypeError("result schema must contain an object")
    schema = cast(dict[str, object], schema_value)
    for path in sorted(Path(directory).rglob("*.json")):
        value = json.loads(path.read_text(encoding="utf-8"))
        if isinstance(value, dict):
            typed = cast(dict[str, object], value)
            if typed.get("schema_version") == 1 and "solver" in typed:
                validate_json(typed, schema)
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


def format_comparison(directory: str | Path) -> str:
    results = collect_results(directory)
    if not results:
        return "No benchmark result JSON files found."
    _assert_matched_identities(results)
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
