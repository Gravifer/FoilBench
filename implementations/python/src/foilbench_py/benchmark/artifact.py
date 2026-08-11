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


def _require_finite(value: object, path: str = "result") -> None:
    if isinstance(value, float):
        if not math.isfinite(value):
            raise ValueError(f"{path} contains a non-finite number")
        return
    if isinstance(value, Mapping):
        for name, child in value.items():
            _require_finite(child, f"{path}.{name}")
        return
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
        for index, child in enumerate(value):
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
    requested = float(cast(float, result["requested_duration"]))
    simulated = float(cast(float, result["simulated_duration"]))
    tolerance = (1.0e-6 if result["precision"] == "float32" else 1.0e-12) * max(
        1.0, abs(requested)
    )
    if success:
        if failure is not None or not isinstance(last_step, Mapping) or not steps:
            raise ValueError("successful benchmark result lacks completed-step semantics")
        if (
            diagnostic_revision != final_revision
            or last_step.get("state_revision") != final_revision
        ):
            raise ValueError("successful benchmark result contains stale revision evidence")
        if abs(simulated - requested) > tolerance:
            raise ValueError("successful benchmark result did not complete requested duration")
    elif not isinstance(failure, Mapping):
        raise ValueError("failed benchmark result lacks structured failure evidence")
    memory_kind = result["memory_measurement"]
    peak_rss = result["peak_rss_bytes"]
    if (memory_kind == "unavailable") != (peak_rss is None):
        raise ValueError("memory measurement kind and RSS value disagree")
