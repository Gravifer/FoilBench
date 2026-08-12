"""Shared benchmark-artifact normalization and semantic validation."""

import math
from collections.abc import Mapping, Sequence
from typing import cast

from foilbench_py.core.models import Scenario

_SOLVER_OPTION_DEFAULTS: dict[str, object] = {
    "initial_condition": "freestream",
    "stable_advection": "maccormack",
    "stable_face_advection": False,
    "stable_cfl": 0.7,
    "pressure_tolerance": 1.0e-5,
    "pressure_max_iterations": 640,
    "pic_flip_blend": 0.95,
    "pic_population_interval": 8,
    "pic_cfl": 0.75,
}


def solver_configuration(scenario: Scenario) -> dict[str, object]:
    """Return the language-neutral numerical configuration recorded in results."""

    return {
        name: scenario.solver_options.get(name, default)
        for name, default in _SOLVER_OPTION_DEFAULTS.items()
    }


def physical_identity(result: Mapping[str, object]) -> dict[str, object]:
    """Return a typed identity whose equality ignores JSON spelling and key order."""

    fields = (
        "bounds",
        "periodic_axes",
        "reynolds",
        "effective_reynolds",
        "solver_configuration",
        "freestream",
        "foil",
        "control_history",
        "requested_duration",
        "output_dt",
        "seed",
    )
    return {field: result[field] for field in fields}


def physical_identities_match(
    left: object, right: object, *, precision: str
) -> bool:
    """Compare physical identities without confusing serialization noise for physics."""

    if isinstance(left, bool) or isinstance(right, bool):
        return type(left) is type(right) and left == right
    if isinstance(left, int) and isinstance(right, int):
        return left == right
    if isinstance(left, (int, float)) and isinstance(right, (int, float)):
        left_value = float(left)
        right_value = float(right)
        tolerance = 2.0e-6 if precision == "float32" else 2.0e-12
        return math.isclose(left_value, right_value, rel_tol=tolerance, abs_tol=tolerance)
    if isinstance(left, Mapping) and isinstance(right, Mapping):
        left_mapping = cast(Mapping[object, object], left)
        right_mapping = cast(Mapping[object, object], right)
        return left_mapping.keys() == right_mapping.keys() and all(
            physical_identities_match(
                left_mapping[key], right_mapping[key], precision=precision
            )
            for key in left_mapping
        )
    if (
        isinstance(left, Sequence)
        and not isinstance(left, (str, bytes, bytearray))
        and isinstance(right, Sequence)
        and not isinstance(right, (str, bytes, bytearray))
    ):
        left_sequence = cast(Sequence[object], left)
        right_sequence = cast(Sequence[object], right)
        return len(left_sequence) == len(right_sequence) and all(
            physical_identities_match(a, b, precision=precision)
            for a, b in zip(left_sequence, right_sequence, strict=True)
        )
    if isinstance(left, str) or isinstance(right, str):
        return isinstance(left, str) and isinstance(right, str) and left == right
    if isinstance(left, bytes) or isinstance(right, bytes):
        return isinstance(left, bytes) and isinstance(right, bytes) and left == right
    if isinstance(left, bytearray) or isinstance(right, bytearray):
        return (
            isinstance(left, bytearray)
            and isinstance(right, bytearray)
            and left == right
        )
    return left is None and right is None


def _require_finite(value: object, path: str = "result") -> None:
    if isinstance(value, float):
        if not math.isfinite(value):
            raise ValueError(f"{path} contains a non-finite number")
        return
    if isinstance(value, Mapping):
        children = cast(Mapping[object, object], value)
        for name, child in children.items():
            _require_finite(child, f"{path}.{name}")
        return
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
        children = cast(Sequence[object], value)
        for index, child in enumerate(children):
            _require_finite(child, f"{path}[{index}]")


def validate_result_semantics(result: Mapping[str, object]) -> None:
    """Enforce cross-field rules that JSON Schema cannot express portably."""

    _require_finite(result)
    success = cast(bool, result["success"])
    failure = result["failure"]
    last_step = result["last_step"]
    final_revision = cast(int, result["final_state_revision"])
    diagnostic_revision = result["diagnostic_state_revision"]
    steps = cast(Sequence[object], result["step_seconds"])
    step_seconds = [float(cast(float, value)) for value in steps]
    requested = float(cast(float, result["requested_duration"]))
    simulated = float(cast(float, result["simulated_duration"]))
    tolerance = (1.0e-6 if result["precision"] == "float32" else 1.0e-12) * max(
        1.0, abs(requested)
    )
    if success:
        if failure is not None or not isinstance(last_step, Mapping) or not steps:
            raise ValueError("successful benchmark result lacks completed-step semantics")
        completed_step = cast(Mapping[str, object], last_step)
        if (
            diagnostic_revision != final_revision
            or completed_step.get("state_revision") != final_revision
        ):
            raise ValueError("successful benchmark result contains stale revision evidence")
        if abs(simulated - requested) > tolerance:
            raise ValueError("successful benchmark result did not complete requested duration")
    elif not isinstance(failure, Mapping):
        raise ValueError("failed benchmark result lacks structured failure evidence")
    if step_seconds:
        ordered = sorted(step_seconds)

        def percentile(fraction: float) -> float:
            position = fraction * (len(ordered) - 1)
            lower = math.floor(position)
            upper = math.ceil(position)
            weight = position - lower
            return ordered[lower] + (ordered[upper] - ordered[lower]) * weight

        total_wall = sum(step_seconds)
        resolution = cast(Sequence[object], result["resolution"])
        cells = math.prod(int(cast(int, value)) for value in resolution)
        substeps = int(cast(int, result["substeps"]))
        diagnostics = cast(Mapping[str, object], result["diagnostics"])
        particle_count = float(cast(float, diagnostics.get("particle_count", 0.0)))
        expected = {
            "median_step_seconds": percentile(0.5),
            "p95_step_seconds": percentile(0.95),
            "simulated_seconds_per_wall_second": simulated / total_wall,
            "cell_updates_per_second": cells * substeps / total_wall,
            "particle_updates_per_second": particle_count * substeps / total_wall,
        }
        for field, expected_value in expected.items():
            actual = float(cast(float, result[field]))
            if not math.isclose(actual, expected_value, rel_tol=1.0e-10, abs_tol=1.0e-12):
                raise ValueError(f"benchmark result contains inconsistent derived field {field}")
    memory_kind = result["memory_measurement"]
    peak_rss = result["peak_rss_bytes"]
    if (memory_kind == "unavailable") != (peak_rss is None):
        raise ValueError("memory measurement kind and RSS value disagree")
