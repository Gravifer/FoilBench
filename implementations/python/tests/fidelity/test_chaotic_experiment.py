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
    retry_cases = cast(dict[str, object], fixture["planning_retry_cases"])
    retry_case = cast(dict[str, object], retry_cases["stable-fluids"])
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


def test_full_size_pic_startup_recovers_a_stale_particle_plan() -> None:
    root = find_repo_root(Path(__file__))
    fixture = cast(
        dict[str, object],
        json.loads(
            (root / "spec" / "conformance" / "solver-validity.json").read_text(
                encoding="utf-8"
            )
        ),
    )
    retry_cases = cast(dict[str, object], fixture["planning_retry_cases"])
    retry_case = cast(dict[str, object], retry_cases["pic-flip"])
    scenario = load_scenario(root / str(retry_case["scenario"]))
    solver = create_solver("pic-flip")
    solver.initialize(scenario, NacaFoil(scenario.foil), scenario.seed)

    report = solver.advance(scenario.control_at(scenario.output_dt), scenario.output_dt)

    assert report.substeps >= 2
    assert int(report.evidence["stability_retries"]) >= 1
    assert float(report.evidence["maximum_particle_cfl"]) <= float(
        scenario.solver_options["pic_cfl"]
    ) * (1.0 + 1.0e-6)
    assert np.isfinite(solver.export_state().velocity).all()
