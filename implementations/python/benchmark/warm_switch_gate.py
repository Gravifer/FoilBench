"""Revision 4 full-resolution directed warm-switch acceptance gate."""

import itertools
import json
from pathlib import Path
from typing import cast

import numpy as np

from foilbench_py.core.geometry import NacaFoil
from foilbench_py.core.models import ControlState
from foilbench_py.core.scenario import find_repo_root, load_scenario
from foilbench_py.core.switching import SolverManager
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
    gate = cast(dict[str, object], fixture["warm_switch"])
    scenario = load_scenario(root / str(gate["scenario"]))
    resolution = tuple(cast(list[int], gate["resolution"]))
    if scenario.domain.resolution != resolution:
        raise ValueError("warm-switch fixture resolution disagrees with its scenario")
    solvers = ("stable-fluids", "lbm-d2q9", "pic-flip")
    for angle, (source, destination) in itertools.product(
        cast(list[float], gate["angles_degrees"]),
        itertools.permutations(solvers, 2),
    ):
        manager = SolverManager(
            create_solver,
            scenario,
            NacaFoil(scenario.foil),
            source,
        )
        source_control = ControlState(scenario.output_dt, angle, 0.0)
        manager.solver.advance(source_control, scenario.output_dt)
        validation = ControlState(2.0 * scenario.output_dt, angle, 0.0)
        outcome = manager.switch(
            destination,
            source_control,
            validation,
            scenario.output_dt,
        )
        state = manager.solver.export_state()
        if not outcome.accepted or not np.isfinite(state.velocity).all():
            raise RuntimeError(f"warm switch failed: {source} -> {destination} at {angle}")
        print(f"passed {source} -> {destination} at {angle:.1f} degrees")


if __name__ == "__main__":
    main()
