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

from foilbench_py.core._schema_adapter import validate_json
from foilbench_py.core.geometry import NacaFoil
from foilbench_py.core.metrics import analyze_wake_probe
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
    scenario_path = root / str(raw["scenario"])
    raw_resolutions = cast(list[list[int]], raw["resolutions"])
    return BenchmarkMatrix(
        id=str(raw["id"]),
        scenario_path=scenario_path,
        solvers=tuple(str(item) for item in cast(list[str], raw["solvers"])),
        resolutions=tuple((int(item[0]), int(item[1])) for item in raw_resolutions),
        duration=float(cast(float, raw["duration"])),
        repetitions=int(cast(int, raw["repetitions"])),
        save_snapshots=bool(raw["save_snapshots"]),
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
                solver = create_solver(solver_id)
                init_start = time.perf_counter()
                solver.initialize(scenario, geometry, scenario.seed)
                initialization_seconds = time.perf_counter() - init_start

                warm_control = scenario.control_at(min(scenario.output_dt, scenario.duration))
                solver.advance(warm_control, min(scenario.output_dt, scenario.duration))
                elapsed_simulated = 0.0
                step_seconds: list[float] = []
                total_substeps = 0
                peak_rss = process.memory_info().rss
                warnings: list[str] = []
                wake_probe: list[float] = []
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
                    while elapsed_simulated < scenario.duration - 1.0e-12:
                        dt = min(scenario.output_dt, scenario.duration - elapsed_simulated)
                        control = scenario.control_at(elapsed_simulated + dt)
                        started = time.perf_counter()
                        report = solver.advance(control, dt)
                        step_seconds.append(time.perf_counter() - started)
                        elapsed_simulated += report.advanced_dt
                        total_substeps += report.substeps
                        warnings.extend(report.warnings)
                        peak_rss = max(peak_rss, process.memory_info().rss)
                        if elapsed_simulated >= 0.5 * scenario.duration:
                            wake_probe.append(float(solver.sample_velocity(probe_point)[0, 1]))
                    diagnostics = solver.diagnostics()
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
                                "wake_dominant_frequency": spectrum.dominant_frequency,
                                "wake_strouhal_number": spectrum.strouhal_number,
                                "wake_dominant_power_fraction": (
                                    spectrum.dominant_power_fraction
                                ),
                            }
                        )
                except (FloatingPointError, RuntimeError, ValueError) as error:
                    success = False
                    warnings.append(f"{type(error).__name__}: {error}")
                    diagnostic_values = {}

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
                    "scenario_id": scenario.id,
                    "language": "python",
                    "solver": solver_id,
                    "git_commit": _git_commit(root),
                    "machine": _machine(),
                    "precision": scenario.precision,
                    "resolution": list(scenario.domain.resolution),
                    "seed": scenario.seed,
                    "initialization_seconds": initialization_seconds,
                    "step_seconds": step_seconds,
                    "median_step_seconds": median,
                    "p95_step_seconds": p95,
                    "simulated_seconds_per_wall_second": (
                        elapsed_simulated / total_wall if total_wall > 0.0 else 0.0
                    ),
                    "cell_updates_per_second": cell_updates_per_second,
                    "particle_updates_per_second": particle_updates_per_second,
                    "peak_rss_bytes": peak_rss,
                    "substeps": total_substeps,
                    "diagnostics": diagnostic_values,
                    "success": success,
                    "warnings": sorted(set(warnings)),
                }
                validate_json(result, result_schema)
                stem = f"{solver_id}-{resolution[0]}x{resolution[1]}-r{repetition + 1}"
                result_path = destination / f"{stem}.json"
                result_path.write_text(json.dumps(result, indent=2), encoding="utf-8")
                if matrix.save_snapshots and success:
                    save_canonical_state(solver.export_state(), destination / f"{stem}-state")
                rows.append(
                    {
                        "solver": solver_id,
                        "resolution": f"{resolution[0]}x{resolution[1]}",
                        "repetition": repetition + 1,
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
