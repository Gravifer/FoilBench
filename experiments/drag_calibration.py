"""Matched raw-solver response matrix for unresolved drag-policy constants."""

import argparse
import json
import math
import time
from dataclasses import replace
from pathlib import Path
from typing import cast

import numpy as np
from foilbench_py.core._schema_adapter import validate_json
from foilbench_py.core.geometry import NacaFoil
from foilbench_py.core.models import ControlKeyframe, ControlState, NumericalFailure
from foilbench_py.core.scenario import find_repo_root, load_scenario
from foilbench_py.solvers.factory import create_solver


def _object(path: Path) -> dict[str, object]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise TypeError(f"{path} must contain an object")
    return cast(dict[str, object], value)


def _run(
    scenario_path: Path,
    resolution: tuple[int, int],
    solver_id: str,
    candidate: dict[str, object],
    trace: dict[str, object],
) -> dict[str, object]:
    base = load_scenario(scenario_path)
    scenario = replace(
        base,
        domain=replace(base.domain, resolution=resolution),
        controls=(ControlKeyframe(0.0, 0.0), ControlKeyframe(base.duration, 0.0)),
    )
    solver = create_solver(solver_id)
    solver.initialize(scenario, NacaFoil(scenario.foil), scenario.seed)
    cap = float(cast(float, candidate["tip_speed_cap"]))
    window = float(cast(float, candidate["smoothing_window_seconds"]))
    samples = cast(list[list[float]], trace["samples"])
    recent: list[tuple[float, float]] = []
    maximum_measured = 0.0
    maximum_solver = 0.0
    maximum_flow = 0.0
    successful = 0
    failure: str | None = None
    physical_time = 0.0
    reference_speed = scenario.reference_speed
    started = time.perf_counter()
    for timestamp, raw_angle in (*samples, [samples[-1][0] + 0.02, samples[-1][1]]):
        angle = float(np.clip(raw_angle, -30.0, 30.0))
        if successful == len(samples):
            measured_degrees = 0.0
        else:
            recent.append((timestamp, angle))
            cutoff = timestamp - window
            while len(recent) > 2 and recent[1][0] < cutoff:
                recent.pop(0)
            measured_degrees = (
                (angle - recent[0][1]) / (timestamp - recent[0][0])
                if len(recent) >= 2 and timestamp > recent[0][0]
                else 0.0
            )
        measured_ratio = (
            abs(math.radians(measured_degrees)) * scenario.foil.chord / reference_speed
        )
        solver_ratio = min(measured_ratio, cap)
        maximum_measured = max(maximum_measured, measured_ratio)
        maximum_solver = max(maximum_solver, solver_ratio)
        omega = math.degrees(solver_ratio * reference_speed / scenario.foil.chord)
        omega = math.copysign(omega, measured_degrees) if measured_degrees else 0.0
        physical_time += scenario.output_dt
        try:
            report = solver.advance(
                ControlState(physical_time, angle, omega),
                scenario.output_dt,
            )
            maximum_flow = max(maximum_flow, report.max_speed)
            successful += 1
        except NumericalFailure as error:
            failure = error.reason
            break
        except (FloatingPointError, ValueError) as error:
            failure = type(error).__name__
            break
    return {
        "candidate": candidate["id"],
        "solver": solver_id,
        "trace": trace["id"],
        "tip_speed_cap": cap,
        "smoothing_window_seconds": window,
        "max_measured_tip_speed_ratio": maximum_measured,
        "max_solver_tip_speed_ratio": maximum_solver,
        "successful_steps": successful,
        "requested_steps": len(samples) + 1,
        "failure_reason": failure,
        "maximum_flow_speed": maximum_flow,
        "wall_seconds": time.perf_counter() - started,
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path)
    arguments = parser.parse_args()
    root = find_repo_root(Path(__file__))
    fixture = _object(root / "spec" / "conformance" / "drag-calibration.json")
    resolution_values = cast(list[int], fixture["resolution"])
    resolution = (resolution_values[0], resolution_values[1])
    scenario_path = root / cast(str, fixture["scenario"])
    runs = [
        _run(scenario_path, resolution, solver, candidate, trace)
        for candidate in cast(list[dict[str, object]], fixture["candidates"])
        for solver in cast(list[str], fixture["solvers"])
        for trace in cast(list[dict[str, object]], fixture["traces"])
    ]
    result: dict[str, object] = {
        "schema_version": 1,
        "contract_id": "foilbench-phase2-v1-drag-calibration",
        "language": "python",
        "scenario": fixture["scenario"],
        "resolution": list(resolution),
        "runs": runs,
    }
    validate_json(
        result, _object(root / "spec" / "drag-calibration-result.schema.json")
    )
    text = json.dumps(result, indent=2)
    if arguments.output is not None:
        arguments.output.parent.mkdir(parents=True, exist_ok=True)
        arguments.output.write_text(text, encoding="utf-8")
    print(text)


if __name__ == "__main__":
    main()
