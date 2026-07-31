from dataclasses import replace
from pathlib import Path

import numpy as np

from foilbench_py.core.geometry import NacaFoil
from foilbench_py.core.scenario import find_repo_root, load_scenario
from foilbench_py.solvers.factory import create_solver


def test_experimental_skew_rk2_path_remains_finite() -> None:
    root = find_repo_root(Path(__file__))
    scenario = load_scenario(root / "scenarios" / "airfoil" / "chaotic-experimental.json")
    scenario = replace(
        scenario,
        domain=replace(scenario.domain, resolution=(64, 38)),
        duration=0.2,
    )
    solver = create_solver("stable-fluids")
    solver.initialize(scenario, NacaFoil(scenario.foil), scenario.seed)

    for step in range(12):
        time = (step + 1) * scenario.output_dt
        solver.advance(scenario.control_at(time), scenario.output_dt)

    state = solver.export_state()
    assert np.isfinite(state.velocity).all()
    assert solver.diagnostics().values["solid_leakage"] < 1.0e-6
