"""Human-readable comparison of schema-compatible benchmark results."""

import json
from pathlib import Path
from typing import cast


def collect_results(directory: str | Path) -> list[dict[str, object]]:
    results: list[dict[str, object]] = []
    for path in sorted(Path(directory).rglob("*.json")):
        value = json.loads(path.read_text(encoding="utf-8"))
        if isinstance(value, dict):
            typed = cast(dict[str, object], value)
            if typed.get("schema_version") == 1 and "solver" in typed:
                results.append(typed)
    return results


def format_comparison(directory: str | Path) -> str:
    results = collect_results(directory)
    if not results:
        return "No benchmark result JSON files found."
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
