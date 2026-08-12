"""Matched-physical-run benchmark harness."""

import csv
import json
import os
import platform
import statistics
import subprocess
import time
from dataclasses import dataclass, replace
from pathlib import Path
from typing import cast

import numpy as np
import psutil

from foilbench_py.benchmark.artifact import solver_configuration, validate_result_semantics
from foilbench_py.core._schema_adapter import validate_json
from foilbench_py.core.geometry import NacaFoil
from foilbench_py.core.metrics import analyze_wake_probe
from foilbench_py.core.models import NumericalFailure, Scenario, StepReport
from foilbench_py.core.protocol import FlowSolver
from foilbench_py.core.scenario import find_repo_root, load_scenario
from foilbench_py.core.state_io import save_canonical_state
from foilbench_py.solvers.factory import create_solver


@dataclass(frozen=True, slots=True)
class BenchmarkMatrix:
    id: str
    scenario_path: Path
    solvers: tuple[str, ...]
    resolutions: tuple[tuple[int, int], ...]
    duration: float
    repetitions: int
    save_snapshots: bool


def _json_object(path: Path) -> dict[str, object]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise TypeError(f"{path} must contain a JSON object")
    return cast(dict[str, object], value)


def load_matrix(path: str | Path) -> BenchmarkMatrix:
    matrix_path = Path(path).resolve()
    raw = _json_object(matrix_path)
    root = find_repo_root(matrix_path)
    validate_json(raw, _json_object(root / "spec" / "benchmark-matrix.schema.json"))
    scenario_path = root / str(raw["scenario"])
    raw_resolutions = cast(list[list[int]], raw["resolutions"])
    return BenchmarkMatrix(
        id=str(raw["id"]),
        scenario_path=scenario_path,
        solvers=tuple(str(item) for item in cast(list[str], raw["solvers"])),
        resolutions=tuple((int(item[0]), int(item[1])) for item in raw_resolutions),
        duration=float(cast(float, raw["duration"])),
        repetitions=int(cast(int, raw["repetitions"])),
        save_snapshots=cast(bool, raw["save_snapshots"]),
    )


def _git_commit(root: Path) -> str:
    try:
        completed = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=root,
            check=True,
            capture_output=True,
            text=True,
        )
        return completed.stdout.strip()
    except (OSError, subprocess.CalledProcessError):
        return "unknown"


def _machine() -> dict[str, object]:
    return {
        "platform": platform.platform(),
        "python": platform.python_version(),
        "processor": platform.processor(),
        "logical_cpus": os.cpu_count(),
        "numpy": np.__version__,
    }


def _percentile_95(values: list[float]) -> float:
    if len(values) == 1:
        return values[0]
    return float(np.percentile(np.asarray(values), 95.0))


type JsonScalar = float | int | bool | str | None


def _json_scalar(value: object) -> JsonScalar:
    if isinstance(value, np.generic):
        return _json_scalar(cast(object, value.item()))
    if isinstance(value, float) and not np.isfinite(value):
        raise ValueError("benchmark evidence values must be finite")
    if value is None or isinstance(value, (bool, int, float, str)):
        return value
    raise TypeError(f"benchmark evidence value {value!r} is not a JSON scalar")


def _step_artifact(report: StepReport | None) -> dict[str, object] | None:
    if report is None:
        return None
    return {
        "requested_dt": report.requested_dt,
        "advanced_dt": report.advanced_dt,
        "substeps": report.substeps,
        "max_speed": report.max_speed,
        "state_revision": report.state_revision,
        "evidence": {key: _json_scalar(value) for key, value in report.evidence.items()},
        "warnings": list(report.warnings),
    }


def _failure_artifact(error: Exception) -> dict[str, object]:
    if isinstance(error, NumericalFailure):
        return {
            "kind": "numerical",
            "reason": error.reason,
            "stage": error.stage,
            "message": str(error),
            "evidence": {
                key: _json_scalar(value) for key, value in error.evidence.items()
            },
        }
    return {
        "kind": "unexpected",
        "reason": None,
        "stage": None,
        "message": f"{type(error).__name__}: {error}",
        "evidence": {},
    }


def recovery_window(scenario: Scenario) -> tuple[float, float] | None:
    """Return the baseline end and completed-return time for an angle excursion."""
    initial_angle = scenario.controls[0].angle_degrees
    final_angle = scenario.controls[-1].angle_degrees
    if not np.isclose(initial_angle, final_angle):
        return None
    changed = [
        index
        for index, keyframe in enumerate(scenario.controls)
        if not np.isclose(keyframe.angle_degrees, initial_angle)
    ]
    if not changed:
        return None
    first_changed = changed[0]
    last_changed = changed[-1]
    if first_changed == 0 or last_changed + 1 >= len(scenario.controls):
        return None
    baseline_end = scenario.controls[first_changed - 1].time
    recovery_start = scenario.controls[last_changed + 1].time
    if baseline_end >= recovery_start or recovery_start >= scenario.duration:
        return None
    return baseline_end, recovery_start


def run_matrix(
    matrix_path: str | Path,
    output_root: str | Path | None = None,
) -> Path:
    matrix = load_matrix(matrix_path)
    root = find_repo_root(Path(matrix_path))
    destination = (
        Path(output_root)
        if output_root is not None
        else root / "results" / matrix.id / time.strftime("%Y%m%d-%H%M%S")
    )
    destination.mkdir(parents=True, exist_ok=True)
    scenario_base = load_scenario(matrix.scenario_path)
    result_schema = _json_object(root / "spec" / "result.schema.json")
    rows: list[dict[str, object]] = []
    process = psutil.Process()

    for resolution in matrix.resolutions:
        scenario = replace(
            scenario_base,
            domain=replace(scenario_base.domain, resolution=resolution),
            duration=matrix.duration,
        )
        geometry = NacaFoil(scenario.foil)
        for solver_id in matrix.solvers:
            for repetition in range(matrix.repetitions):
                initialization_seconds = 0.0
                cold_step_seconds = 0.0
                solver: FlowSolver | None = None
                elapsed_simulated = 0.0
                step_seconds: list[float] = []
                total_substeps = 0
                peak_rss = process.memory_info().rss
                warnings: list[str] = []
                wake_probe: list[float] = []
                recovery_times = recovery_window(scenario)
                recovery_baseline: tuple[float, float] | None = None
                recovery_elapsed: float | None = None
                last_report: StepReport | None = None
                diagnostic_revision: int | None = None
                failure: dict[str, object] | None = None
                diagnostic_values: dict[str, float] = {}
                probe_point = np.asarray(
                    [
                        [
                            min(
                                scenario.foil.pivot[0] + 1.5 * scenario.foil.chord,
                                scenario.domain.bounds[0][1] - 0.5 * scenario.domain.dx,
                            ),
                            scenario.foil.pivot[1],
                        ]
                    ],
                    dtype=scenario.dtype,
                )
                success = True
                try:
                    cold_solver = create_solver(solver_id)
                    init_start = time.perf_counter()
                    cold_solver.initialize(scenario, geometry, scenario.seed)
                    initialization_seconds = time.perf_counter() - init_start

                    warm_dt = min(scenario.output_dt, scenario.duration)
                    warm_control = scenario.control_at(warm_dt)
                    cold_step_start = time.perf_counter()
                    cold_solver.advance(warm_control, warm_dt)
                    cold_step_seconds = time.perf_counter() - cold_step_start

                    # Reinitialize after warming process-global compiled kernels so the
                    # measured physical run starts from the declared scenario state.
                    solver = create_solver(solver_id)
                    solver.initialize(scenario, geometry, scenario.seed)
                    while elapsed_simulated < scenario.duration - 1.0e-12:
                        dt = min(scenario.output_dt, scenario.duration - elapsed_simulated)
                        control = scenario.control_at(elapsed_simulated + dt)
                        started = time.perf_counter()
                        report = solver.advance(control, dt)
                        last_report = report
                        step_seconds.append(time.perf_counter() - started)
                        elapsed_simulated += report.advanced_dt
                        total_substeps += report.substeps
                        warnings.extend(report.warnings)
                        peak_rss = max(peak_rss, process.memory_info().rss)
                        if elapsed_simulated >= 0.5 * scenario.duration:
                            wake_probe.append(float(solver.sample_velocity(probe_point)[0, 1]))
                        if recovery_times is not None:
                            baseline_end, recovery_start = recovery_times
                            crossed_baseline = (
                                recovery_baseline is None
                                and elapsed_simulated >= baseline_end
                            )
                            observing_recovery = (
                                recovery_baseline is not None
                                and recovery_elapsed is None
                                and elapsed_simulated >= recovery_start
                            )
                            if crossed_baseline or observing_recovery:
                                transient = solver.diagnostics().values
                                wake = transient["wake_width"]
                                recirculation = transient["recirculation_area"]
                                if crossed_baseline:
                                    recovery_baseline = (wake, recirculation)
                                elif recovery_baseline is not None:
                                    wake_tolerance = max(
                                        1.25 * recovery_baseline[0],
                                        2.0 * scenario.domain.dy,
                                    )
                                    recirculation_tolerance = max(
                                        1.25 * recovery_baseline[1],
                                        2.0 * scenario.domain.dx * scenario.domain.dy,
                                    )
                                    if (
                                        wake <= wake_tolerance
                                        and recirculation <= recirculation_tolerance
                                    ):
                                        recovery_elapsed = elapsed_simulated - recovery_start
                    diagnostics = solver.diagnostics()
                    if diagnostics.state_revision != solver.state_revision:
                        raise RuntimeError("benchmark diagnostics describe a stale state revision")
                    diagnostic_revision = diagnostics.state_revision
                    warnings.extend(diagnostics.warnings)
                    diagnostic_values = dict(diagnostics.values)
                    if len(wake_probe) >= 8:
                        spectrum = analyze_wake_probe(
                            np.asarray(wake_probe, dtype=np.float64),
                            scenario.output_dt,
                            scenario.foil.chord,
                            max(float(np.linalg.norm(scenario.freestream)), 1.0e-12),
                        )
                        diagnostic_values.update(
                            {
                                "wake_probe_samples": float(spectrum.sample_count),
                                "wake_frequency_resolution": spectrum.frequency_resolution,
                                "wake_transverse_rms": spectrum.transverse_rms,
                                "wake_mixing_index": (
                                    spectrum.transverse_rms / scenario.reference_speed
                                ),
                                "wake_dominant_frequency": spectrum.dominant_frequency,
                                "wake_strouhal_number": spectrum.strouhal_number,
                                "wake_dominant_power_fraction": (
                                    spectrum.dominant_power_fraction
                                ),
                            }
                        )
                    if recovery_times is not None and recovery_baseline is not None:
                        baseline_end, recovery_start = recovery_times
                        observed = recovery_elapsed is not None
                        diagnostic_values.update(
                            {
                                "recovery_baseline_time": baseline_end,
                                "recovery_start_time": recovery_start,
                                "recovery_observed": float(observed),
                                "recovery_elapsed": (
                                    recovery_elapsed
                                    if recovery_elapsed is not None
                                    else scenario.duration - recovery_start
                                ),
                            }
                        )
                        if not observed:
                            warnings.append(
                                "wake recovery was not observed; recovery_elapsed is right-censored"
                            )
                except Exception as error:
                    success = False
                    warnings.append(f"{type(error).__name__}: {error}")
                    diagnostic_values = {}
                    diagnostic_revision = None
                    failure = _failure_artifact(error)

                total_wall = sum(step_seconds)
                median = statistics.median(step_seconds) if step_seconds else 0.0
                p95 = _percentile_95(step_seconds) if step_seconds else 0.0
                cell_updates_per_second = (
                    scenario.domain.nx * scenario.domain.ny * total_substeps / total_wall
                    if total_wall > 0.0
                    else 0.0
                )
                particle_count = diagnostic_values.get("particle_count", 0.0)
                particle_updates_per_second = (
                    particle_count * total_substeps / total_wall if total_wall > 0.0 else 0.0
                )
                result: dict[str, object] = {
                    "schema_version": 1,
                    "contract_id": "foilbench-phase2-v1",
                    "contract_revision": 4,
                    "benchmark_matrix_id": matrix.id,
                    "scenario_id": scenario.id,
                    "repetition": repetition + 1,
                    "language": "python",
                    "solver": solver_id,
                    "git_commit": _git_commit(root),
                    "machine": _machine(),
                    "precision": scenario.precision,
                    "resolution": list(scenario.domain.resolution),
                    "bounds": [list(pair) for pair in scenario.domain.bounds],
                    "periodic_axes": list(scenario.domain.periodic_axes),
                    "reynolds": scenario.reynolds,
                    "effective_reynolds": diagnostic_values.get(
                        "effective_reynolds", scenario.reynolds
                    ),
                    "solver_configuration": solver_configuration(scenario),
                    "freestream": list(scenario.freestream),
                    "foil": {
                        "naca": scenario.foil.naca,
                        "chord": scenario.foil.chord,
                        "pivot": list(scenario.foil.pivot),
                    },
                    "control_history": [
                        {"time": keyframe.time, "angle_degrees": keyframe.angle_degrees}
                        for keyframe in scenario.controls
                    ],
                    "requested_duration": matrix.duration,
                    "simulated_duration": elapsed_simulated,
                    "output_dt": scenario.output_dt,
                    "seed": scenario.seed,
                    "initialization_seconds": initialization_seconds,
                    "cold_step_seconds": cold_step_seconds,
                    "step_seconds": step_seconds,
                    "median_step_seconds": median,
                    "p95_step_seconds": p95,
                    "simulated_seconds_per_wall_second": (
                        elapsed_simulated / total_wall if total_wall > 0.0 else 0.0
                    ),
                    "cell_updates_per_second": cell_updates_per_second,
                    "particle_updates_per_second": particle_updates_per_second,
                    "peak_rss_bytes": peak_rss,
                    "memory_measurement": "rss",
                    "runtime_startup_seconds": None,
                    "worker_startup_seconds": None,
                    "substeps": total_substeps,
                    "final_state_revision": 0 if solver is None else solver.state_revision,
                    "diagnostic_state_revision": diagnostic_revision,
                    "last_step": _step_artifact(last_report),
                    "diagnostics": diagnostic_values,
                    "success": success,
                    "failure": failure,
                    "warnings": sorted(set(warnings)),
                }
                validate_json(result, result_schema)
                validate_result_semantics(result)
                stem = f"{solver_id}-{resolution[0]}x{resolution[1]}-r{repetition + 1}"
                result_path = destination / f"{stem}.json"
                result_path.write_text(
                    json.dumps(result, indent=2, allow_nan=False), encoding="utf-8"
                )
                if matrix.save_snapshots and success and solver is not None:
                    save_canonical_state(solver.export_state(), destination / f"{stem}-state")
                rows.append(
                    {
                        "solver": solver_id,
                        "resolution": f"{resolution[0]}x{resolution[1]}",
                        "repetition": repetition + 1,
                        "initialization_seconds": initialization_seconds,
                        "cold_step_seconds": cold_step_seconds,
                        "median_step_seconds": median,
                        "p95_step_seconds": p95,
                        "simulated_seconds_per_wall_second": result[
                            "simulated_seconds_per_wall_second"
                        ],
                        "cell_updates_per_second": cell_updates_per_second,
                        "particle_updates_per_second": particle_updates_per_second,
                        "peak_rss_bytes": peak_rss,
                        "success": success,
                    }
                )

    if rows:
        with (destination / "summary.csv").open("w", newline="", encoding="utf-8") as handle:
            writer = csv.DictWriter(handle, fieldnames=list(rows[0]))
            writer.writeheader()
            writer.writerows(rows)
    return destination
