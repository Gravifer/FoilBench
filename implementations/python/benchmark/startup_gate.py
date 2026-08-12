"""Revision 4 full-resolution cold-start acceptance gate."""

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
    gate = cast(dict[str, object], fixture["startup"])
    scenario = load_scenario(root / str(gate["scenario"]))
    resolution = tuple(cast(list[int], gate["resolution"]))
    if scenario.domain.resolution != resolution:
        raise ValueError("startup fixture resolution disagrees with its scenario")
    steps = int(cast(int, gate["steps"]))
    if steps < 1:
        raise ValueError("startup gate requires at least one step")
    for solver_id in cast(list[str], gate["solvers"]):
        solver = create_solver(solver_id)
        solver.initialize(scenario, NacaFoil(scenario.foil), scenario.seed)
        for step in range(1, steps + 1):
            target_time = step * scenario.output_dt
            report = solver.advance(
                scenario.control_at(target_time), scenario.output_dt
            )
            if not np.isclose(report.advanced_dt, scenario.output_dt):
                raise RuntimeError(f"{solver_id} violated requested startup time")
        state = solver.export_state()
        diagnostics = solver.diagnostics()
        if not np.isfinite(state.velocity).all():
            raise RuntimeError(f"{solver_id} produced a non-finite startup state")
        if diagnostics.state_revision != solver.state_revision:
            raise RuntimeError(f"{solver_id} produced stale startup diagnostics")
        print(f"{solver_id:<18}passed {steps} startup step(s)")


if __name__ == "__main__":
    main()
