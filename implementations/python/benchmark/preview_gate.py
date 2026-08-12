"""Revision 4 warmed-step preview acceptance gate."""

import json
import statistics
from pathlib import Path
from time import perf_counter
from typing import cast

from foilbench_py.core.geometry import NacaFoil
from foilbench_py.core.scenario import find_repo_root, load_scenario
from foilbench_py.solvers.factory import create_solver


def main() -> None:
    root = find_repo_root(Path(__file__))
    fixture = cast(
        dict[str, object],
        json.loads(
            (root / "spec/conformance/fullsize-acceptance.json").read_text(
                encoding="utf-8"
            )
        ),
    )
    preview = cast(dict[str, object], fixture["preview"])
    scenario = load_scenario(root / str(preview["scenario"]))
    expected_resolution = tuple(cast(list[int], preview["resolution"]))
    if scenario.domain.resolution != expected_resolution:
        raise ValueError("preview fixture resolution disagrees with its scenario")
    minimum_rate = float(cast(float, preview["minimum_warmed_solver_steps_per_second"]))
    failures: list[str] = []
    for solver_id in cast(list[str], preview["solvers"]):
        solver = create_solver(solver_id)
        solver.initialize(scenario, NacaFoil(scenario.foil), scenario.seed)
        for step in range(1, 8):
            solver.advance(scenario.control_at(step * scenario.output_dt), scenario.output_dt)
        timings: list[float] = []
        for step in range(8, 28):
            started = perf_counter()
            solver.advance(scenario.control_at(step * scenario.output_dt), scenario.output_dt)
            timings.append(perf_counter() - started)
        median_rate = 1.0 / statistics.median(timings)
        print(f"{solver_id:<18}{median_rate:>8.2f} median solver steps/s")
        if median_rate < minimum_rate:
            failures.append(f"{solver_id}: {median_rate:.2f} < {minimum_rate:.2f}")
    if failures:
        raise RuntimeError("Python preview gate failed:\n" + "\n".join(failures))


if __name__ == "__main__":
    main()
