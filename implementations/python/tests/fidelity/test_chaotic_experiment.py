import json
from dataclasses import replace
from pathlib import Path
from typing import cast

import numpy as np

from foilbench_py.core.geometry import NacaFoil
from foilbench_py.core.models import ControlState, RestartState
from foilbench_py.core.scenario import find_repo_root, load_scenario
from foilbench_py.solvers.factory import create_solver


def _retry_scenario(root: Path, retry_case: dict[str, object]):
    scenario = load_scenario(root / str(retry_case["scenario"]))
    resolution = tuple(int(value) for value in cast(list[int], retry_case["resolution"]))
    options = dict(scenario.solver_options)
    options.update(cast(dict[str, object], retry_case["solver_options"]))
    return replace(
        scenario,
        domain=replace(scenario.domain, resolution=resolution),
        output_dt=float(cast(float, retry_case["target_dt"])),
        solver_options=options,
    )


def test_stable_fluids_retries_a_stale_motion_plan() -> None:
    root = find_repo_root(Path(__file__))
    fixture = cast(
        dict[str, object],
        json.loads(
            (root / "spec" / "conformance" / "solver-validity.json").read_text(encoding="utf-8")
        ),
    )
    retry_cases = cast(dict[str, object], fixture["planning_retry_cases"])
    retry_case = cast(dict[str, object], retry_cases["stable-fluids"])
    scenario = _retry_scenario(root, retry_case)
    solver = create_solver("stable-fluids")
    solver.initialize(scenario, NacaFoil(scenario.foil), scenario.seed)

    total_retries = 0
    target_dt = float(cast(float, retry_case["target_dt"]))
    expected_steps = int(cast(int, retry_case["expected_steps"]))
    assert expected_steps >= 1
    for step in range(expected_steps):
        control = ControlState(
            (step + 1) * target_dt,
            float(cast(float, retry_case["angle_degrees"])),
            float(cast(float, retry_case["angular_velocity_degrees"])),
        )
        report = solver.advance(control, target_dt)
        assert report.state_revision == step + 1
        total_retries += int(report.evidence["stability_retries"])

    state = solver.export_state()
    assert solver.state_revision == expected_steps
    assert np.isclose(state.time, expected_steps * target_dt)
    assert total_retries >= int(cast(int, retry_case["minimum_total_stability_retries"]))
    assert np.isfinite(state.velocity).all()
    assert solver.diagnostics().values["solid_leakage"] < 1.0e-6


def test_full_size_pic_startup_respects_the_configured_particle_cfl() -> None:
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
    scenario = _retry_scenario(root, retry_case)
    solver = create_solver("pic-flip")
    solver.initialize(scenario, NacaFoil(scenario.foil), scenario.seed)

    target_dt = float(cast(float, retry_case["target_dt"]))
    expected_steps = int(cast(int, retry_case["expected_steps"]))
    assert expected_steps >= 1
    total_retries = 0
    report = None
    for step in range(expected_steps):
        report = solver.advance(
            ControlState(
                (step + 1) * target_dt,
                float(cast(float, retry_case["angle_degrees"])),
                float(cast(float, retry_case["angular_velocity_degrees"])),
            ),
            target_dt,
        )
        total_retries += int(report.evidence["stability_retries"])

    assert report is not None
    assert report.substeps >= 1
    assert solver.state_revision == expected_steps
    assert total_retries >= int(
        cast(int, retry_case["minimum_total_stability_retries"])
    )
    maximum_particle_cfl = cast(float, report.evidence["maximum_particle_cfl"])
    configured_cfl = cast(float, scenario.solver_options["pic_cfl"])
    assert maximum_particle_cfl <= configured_cfl * (1.0 + 1.0e-6)
    assert np.isfinite(solver.export_state().velocity).all()


def test_full_size_lbm_startup_retries_a_stale_mach_plan() -> None:
    root = find_repo_root(Path(__file__))
    fixture = cast(
        dict[str, object],
        json.loads(
            (root / "spec" / "conformance" / "solver-validity.json").read_text(
                encoding="utf-8"
            )
        ),
    )
    retry_case = cast(
        dict[str, object],
        cast(dict[str, object], fixture["planning_retry_cases"])["lbm-d2q9"],
    )
    scenario = load_scenario(root / str(retry_case["scenario"]))
    solver = create_solver("lbm-d2q9")
    angle = float(cast(float, retry_case["angle_degrees"]))
    solver.restart(
        scenario,
        NacaFoil(scenario.foil),
        scenario.seed,
        RestartState(0.0, angle, scenario.reynolds),
    )
    expected_steps = int(cast(int, retry_case["expected_steps"]))
    assert expected_steps >= 1
    total_retries = 0
    report = None
    for step in range(expected_steps):
        report = solver.advance(
            ControlState((step + 1) * scenario.output_dt, angle, 0.0),
            scenario.output_dt,
        )
        total_retries += int(report.evidence["stability_retries"])
    assert report is not None
    assert solver.state_revision == expected_steps
    assert total_retries >= int(
        cast(int, retry_case["minimum_total_stability_retries"])
    )
    assert float(report.evidence["maximum_lattice_mach"]) <= 0.08 * (1.0 + 1.0e-6)
