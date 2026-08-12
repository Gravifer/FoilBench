import json
from pathlib import Path
from typing import cast

import numpy as np

from foilbench_py.core.geometry import NacaFoil
from foilbench_py.core.scenario import find_repo_root, load_scenario
from foilbench_py.solvers.factory import create_solver


def test_experimental_skew_rk2_path_remains_finite() -> None:
    root = find_repo_root(Path(__file__))
    fixture = cast(
        dict[str, object],
        json.loads(
            (root / "spec" / "conformance" / "solver-validity.json").read_text(encoding="utf-8")
        ),
    )
    retry_case = cast(dict[str, object], fixture["stable_retry_case"])
    scenario = load_scenario(root / "scenarios" / "airfoil" / "chaotic-experimental.json")
    solver = create_solver("stable-fluids")
    solver.initialize(scenario, NacaFoil(scenario.foil), scenario.seed)

    total_retries = 0
    expected_steps = int(cast(int, retry_case["expected_steps"]))
    for step in range(expected_steps):
        time = (step + 1) * scenario.output_dt
        report = solver.advance(scenario.control_at(time), scenario.output_dt)
        assert report.state_revision == step + 1
        total_retries += int(report.evidence["stability_retries"])

    state = solver.export_state()
    assert np.isclose(state.time, float(cast(float, retry_case["duration"])))
    assert total_retries >= int(cast(int, retry_case["minimum_total_stability_retries"]))
    assert np.isfinite(state.velocity).all()
    assert solver.diagnostics().values["solid_leakage"] < 1.0e-6
