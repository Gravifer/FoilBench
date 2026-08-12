"""Revision 4 full-resolution scheduled-control acceptance gate."""

import json
from pathlib import Path
from typing import cast

import numpy as np

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
    gate = cast(dict[str, object], fixture["scheduled_checkpoints"])
    scenario = load_scenario(root / str(gate["scenario"]))
    resolution = tuple(cast(list[int], gate["resolution"]))
    if scenario.domain.resolution != resolution:
        raise ValueError("scheduled fixture resolution disagrees with its scenario")
    solver = create_solver(str(gate["solver"]))
    solver.initialize(scenario, NacaFoil(scenario.foil), scenario.seed)
    checkpoints = [float(value) for value in cast(list[float], gate["times"])]
    checkpoint_steps = {round(value / scenario.output_dt): value for value in checkpoints}
    for step in range(1, max(checkpoint_steps) + 1):
        time = step * scenario.output_dt
        solver.advance(scenario.control_at(time), scenario.output_dt)
        if step in checkpoint_steps:
            state = solver.export_state()
            if not np.isfinite(state.velocity).all():
                raise RuntimeError(f"non-finite scheduled state at t={time}")
            print(f"passed scheduled checkpoint t={time:.2f}")


if __name__ == "__main__":
    main()
