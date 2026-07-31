"""Finite-amplitude sensitivity test for the provisional chaotic wake."""

import argparse
import json
import time
from dataclasses import replace
from pathlib import Path

import numpy as np
from chaotic_wake_sweep import SweepCase, _scenario

from foilbench_py.core.geometry import NacaFoil, cell_centers
from foilbench_py.core.scenario import find_repo_root, load_scenario
from foilbench_py.solvers.factory import create_solver


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser()
    parser.add_argument("--duration", type=float, default=12.0)
    parser.add_argument("--epsilon", type=float, default=1.0e-5)
    parser.add_argument("--output", type=Path)
    parser.add_argument(
        "--scenario",
        type=Path,
        default=Path("scenarios/airfoil/reference.json"),
    )
    parser.add_argument(
        "--single",
        nargs=4,
        type=float,
        default=(10_000.0, 35.0, 256.0, 128.0),
        metavar=("RE", "ANGLE", "NX", "NY"),
    )
    parser.add_argument("--scheme", choices=("maccormack", "skew-rk2"), default="maccormack")
    return parser


def _fit_exponential(
    times: np.ndarray,
    differences: np.ndarray,
    initial: float,
) -> tuple[float, float, int]:
    selected = (differences >= 1.5 * initial) & (differences <= 0.02) & np.isfinite(differences)
    if np.count_nonzero(selected) < 8:
        return 0.0, 0.0, int(np.count_nonzero(selected))
    selected_times = times[selected]
    logarithm = np.log(differences[selected])
    coefficients = np.polyfit(selected_times, logarithm, 1)
    prediction = np.polyval(coefficients, selected_times)
    residual = float(np.sum((logarithm - prediction) ** 2))
    total = float(np.sum((logarithm - np.mean(logarithm)) ** 2))
    r_squared = 1.0 - residual / max(total, np.finfo(np.float64).tiny)
    return float(coefficients[0]), r_squared, int(np.count_nonzero(selected))


def main() -> None:
    arguments = _parser().parse_args()
    root = find_repo_root(Path(__file__))
    base = load_scenario(root / arguments.scenario)
    if arguments.scheme == "skew-rk2":
        base = replace(
            base,
            solver_options={**base.solver_options, "stable_advection": "skew-rk2"},
        )
    reynolds, angle, nx, ny = arguments.single
    case = SweepCase(reynolds, angle, (int(nx), int(ny)))
    scenario = _scenario(base, case, arguments.duration)
    geometry = NacaFoil(scenario.foil)
    reference = create_solver("stable-fluids")
    perturbed = create_solver("stable-fluids")
    reference.initialize(scenario, geometry, scenario.seed)
    perturbed.initialize(scenario, geometry, scenario.seed)

    control = scenario.control_at(0.0)
    reference_state = reference.export_state()
    reference.import_state(reference_state, control)
    state = perturbed.export_state()
    positions = cell_centers(scenario.domain)
    x = positions[:, :, 0]
    y = positions[:, :, 1]
    streamfunction = (
        np.exp(-(((x - 0.2) / 0.8) ** 2) - ((y - 0.25) / 0.5) ** 2)
        * np.sin(2.0 * np.pi * (x - scenario.domain.bounds[0][0]) / 1.3)
        * np.sin(2.0 * np.pi * (y - scenario.domain.bounds[1][0]) / 0.9)
    )
    perturbation = np.stack(
        (
            np.gradient(streamfunction, scenario.domain.dy, axis=0),
            -np.gradient(streamfunction, scenario.domain.dx, axis=1),
        ),
        axis=2,
    )
    perturbation[geometry.mask(scenario.domain, case.angle_degrees)] = 0.0
    perturbation *= arguments.epsilon / np.max(np.linalg.norm(perturbation, axis=2))
    velocity = state.velocity.copy()
    velocity[0] += np.asarray(perturbation, dtype=scenario.dtype)
    perturbed.import_state(
        replace(state, source_solver="deterministic-perturbation", velocity=velocity),
        control,
    )

    wake = (x > scenario.foil.pivot[0]) & ~geometry.mask(scenario.domain, case.angle_degrees)
    initial_difference = float(
        np.sqrt(
            np.mean(
                (
                    perturbed.export_state().velocity[0][wake]
                    - reference.export_state().velocity[0][wake]
                )
                ** 2
            )
        )
    )
    times: list[float] = []
    differences: list[float] = []
    simulated = 0.0
    started = time.perf_counter()
    while simulated < scenario.duration - 1.0e-12:
        dt = min(scenario.output_dt, scenario.duration - simulated)
        simulated += dt
        control = scenario.control_at(simulated)
        reference.advance(control, dt)
        perturbed.advance(control, dt)
        reference_velocity = reference.export_state().velocity[0]
        perturbed_velocity = perturbed.export_state().velocity[0]
        differences.append(
            float(np.sqrt(np.mean((perturbed_velocity[wake] - reference_velocity[wake]) ** 2)))
        )
        times.append(simulated)
    wall_seconds = time.perf_counter() - started
    time_array = np.asarray(times, dtype=np.float64)
    difference_array = np.asarray(differences, dtype=np.float64)
    exponent, r_squared, fit_samples = _fit_exponential(
        time_array, difference_array, initial_difference
    )
    result: dict[str, object] = {
        "scenario": scenario.id,
        "epsilon": arguments.epsilon,
        "initial_wake_rms_difference": initial_difference,
        "final_wake_rms_difference": float(difference_array[-1]),
        "maximum_wake_rms_difference": float(np.max(difference_array)),
        "amplification": float(np.max(difference_array) / initial_difference),
        "finite_time_exponent": exponent,
        "exponential_fit_r_squared": r_squared,
        "exponential_fit_samples": fit_samples,
        "wall_seconds": wall_seconds,
        "times": times,
        "wake_rms_differences": differences,
    }
    print(
        json.dumps(
            {
                key: value
                for key, value in result.items()
                if key not in ("times", "wake_rms_differences")
            }
        )
    )
    if arguments.output is not None:
        arguments.output.parent.mkdir(parents=True, exist_ok=True)
        arguments.output.write_text(json.dumps(result, indent=2), encoding="utf-8")


if __name__ == "__main__":
    main()
