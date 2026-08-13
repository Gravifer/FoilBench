import json
from dataclasses import replace
from pathlib import Path
from typing import cast

import numpy as np
import pytest

from foilbench_py.core.geometry import NacaFoil, cell_centers
from foilbench_py.core.metrics import vorticity
from foilbench_py.core.models import ControlKeyframe, DomainSpec, Scenario
from foilbench_py.core.scenario import find_repo_root, load_scenario
from foilbench_py.solvers.factory import create_solver, solver_ids
from tests.helpers import ScenarioFactory

ROOT = find_repo_root(Path(__file__))
FIDELITY = cast(
    dict[str, object],
    json.loads(
        (ROOT / "spec/proposals/revision5/fixtures/fidelity-cases.json").read_text(
            encoding="utf-8"
        )
    ),
)


def _case(case_id: str) -> dict[str, object]:
    for case in cast(list[dict[str, object]], FIDELITY["cases"]):
        if case["id"] == case_id:
            return case
    raise KeyError(case_id)


def _fidelity_scenario(case_id: str) -> Scenario:
    case = _case(case_id)
    scenario = load_scenario(ROOT / str(case["scenario"]))
    resolution_values = cast(list[int], case["resolution"])
    return replace(
        scenario,
        domain=replace(scenario.domain, resolution=tuple(resolution_values)),
        duration=float(cast(float, case["duration"])),
    )


def _threshold(case_id: str, metric: str) -> float:
    metrics = cast(dict[str, dict[str, object]], _case(case_id)["metrics"])
    return float(cast(float, metrics[metric]["threshold"]))


@pytest.mark.fidelity
@pytest.mark.parametrize("solver_id", solver_ids())
def test_uniform_flow_remains_finite_and_near_uniform(
    solver_id: str,
) -> None:
    scenario = _fidelity_scenario("uniform")
    geometry = NacaFoil(scenario.foil)
    solver = create_solver(solver_id)
    solver.initialize(scenario, geometry, 0)
    before = solver.export_state()
    for step in range(round(scenario.duration / scenario.output_dt)):
        solver.advance(
            scenario.control_at((step + 1) * scenario.output_dt), scenario.output_dt
        )
    after = solver.export_state()
    velocity_drift = float(np.sqrt(np.mean((after.velocity - before.velocity) ** 2)))
    spurious_vorticity = float(
        np.sqrt(np.mean(vorticity(after.velocity[0], scenario.domain) ** 2))
    )
    density_drift = (
        0.0
        if after.density is None or before.density is None
        else float(np.max(np.abs(after.density - before.density)))
    )
    assert velocity_drift < _threshold("uniform", "velocity_rms_drift")
    assert spurious_vorticity < _threshold("uniform", "spurious_vorticity_rms")
    assert density_drift < _threshold("uniform", "density_linf_drift")


@pytest.mark.fidelity
@pytest.mark.parametrize("solver_id", solver_ids())
def test_taylor_green_velocity_error_and_energy_decay(solver_id: str) -> None:
    scenario = _fidelity_scenario("taylor-green")
    solver = create_solver(solver_id)
    solver.initialize(scenario, NacaFoil(scenario.foil), 0)
    before = solver.diagnostics().values["kinetic_energy"]
    for step in range(round(scenario.duration / scenario.output_dt)):
        solver.advance(
            scenario.control_at((step + 1) * scenario.output_dt), scenario.output_dt
        )
    positions = cell_centers(scenario.domain)
    initial = np.stack(
        (
            np.sin(positions[:, :, 0]) * np.cos(positions[:, :, 1]),
            -np.cos(positions[:, :, 0]) * np.sin(positions[:, :, 1]),
        ),
        axis=2,
    )
    viscosity = scenario.reference_speed * scenario.foil.chord / scenario.reynolds
    expected = initial * np.exp(-2.0 * viscosity * scenario.duration)
    actual = solver.export_state().velocity[0]
    mean_square_error = cast(np.floating, np.mean((actual - expected) ** 2))
    velocity_l2_error = float(np.sqrt(mean_square_error))
    after = solver.diagnostics().values["kinetic_energy"]
    assert velocity_l2_error < _threshold("taylor-green", "velocity_l2_error")
    assert 0.0 <= after <= before * _threshold("taylor-green", "kinetic_energy_ratio")


@pytest.mark.fidelity
@pytest.mark.parametrize("solver_id", solver_ids())
def test_poiseuille_profile_has_faster_center(
    solver_id: str,
) -> None:
    scenario = _fidelity_scenario("poiseuille")
    solver = create_solver(solver_id)
    solver.initialize(scenario, NacaFoil(scenario.foil), 0)
    for step in range(round(scenario.duration / scenario.output_dt)):
        solver.advance(
            scenario.control_at((step + 1) * scenario.output_dt), scenario.output_dt
        )
    velocity = solver.export_state().velocity[0]
    positions = cell_centers(scenario.domain)
    y0, y1 = scenario.domain.bounds[1]
    radius = 0.5 * (y1 - y0)
    center_y = 0.5 * (y0 + y1)
    expected = 1.5 * (1.0 - ((positions[:, :, 1] - center_y) / radius) ** 2)
    profile_mean_square_error = cast(
        np.floating,
        np.mean((velocity[:, :, 0] - expected) ** 2),
    )
    profile_error = float(np.sqrt(profile_mean_square_error))
    center = float(
        cast(np.floating, np.mean(velocity[velocity.shape[0] // 2, :, 0]))
    )
    lower = float(cast(np.floating, np.mean(velocity[0, :, 0])))
    upper = float(cast(np.floating, np.mean(velocity[-1, :, 0])))
    walls = 0.5 * (lower + upper)
    normal_wall_leakage = max(
        float(np.max(np.abs(velocity[0, :, 1]))),
        float(np.max(np.abs(velocity[-1, :, 1]))),
    )
    assert center > walls
    assert profile_error < _threshold("poiseuille", "profile_l2_error")
    assert normal_wall_leakage < _threshold("poiseuille", "normal_wall_leakage")


@pytest.mark.parametrize("solver_id", solver_ids())
def test_naca0012_zero_angle_is_symmetric_and_impenetrable(solver_id: str) -> None:
    scenario = _fidelity_scenario("naca0012-zero")
    solver = create_solver(solver_id)
    solver.initialize(scenario, NacaFoil(scenario.foil), scenario.seed)
    for step in range(round(scenario.duration / scenario.output_dt)):
        time = (step + 1) * scenario.output_dt
        solver.advance(scenario.control_at(time), scenario.output_dt)
    velocity = solver.export_state().velocity[0]
    streamwise_error = cast(
        np.floating,
        np.mean((velocity[:, :, 0] - velocity[::-1, :, 0]) ** 2),
    )
    transverse_error = cast(
        np.floating,
        np.mean((velocity[:, :, 1] + velocity[::-1, :, 1]) ** 2),
    )
    symmetry_error = float(np.sqrt(streamwise_error + transverse_error))
    assert symmetry_error < _threshold("naca0012-zero", "symmetry_l2_error")
    assert solver.diagnostics().values["solid_leakage"] < _threshold(
        "naca0012-zero", "solid_leakage"
    )


@pytest.mark.fidelity
@pytest.mark.parametrize("solver_id", solver_ids())
def test_dynamic_naca_reports_declared_finite_metrics(solver_id: str) -> None:
    scenario = _fidelity_scenario("naca2412-dynamic")
    solver = create_solver(solver_id)
    solver.initialize(scenario, NacaFoil(scenario.foil), scenario.seed)
    for step in range(round(scenario.duration / scenario.output_dt)):
        time = (step + 1) * scenario.output_dt
        solver.advance(scenario.control_at(time), scenario.output_dt)
    diagnostics = solver.diagnostics().values
    for metric in cast(dict[str, object], _case("naca2412-dynamic")["metrics"]):
        assert np.isfinite(diagnostics[metric])


@pytest.mark.fidelity
def test_full_stall_develops_an_unsteady_wake(
    scenario_factory: ScenarioFactory,
) -> None:
    scenario = scenario_factory(resolution=(80, 48))
    scenario = replace(
        scenario,
        domain=DomainSpec(
            2,
            ((-1.5, 3.5), (-1.5, 1.5)),
            (80, 48),
        ),
        reynolds=1000.0,
        controls=(ControlKeyframe(0.0, 25.0),),
        solver_options={
            **scenario.solver_options,
            "pressure_tolerance": 0.002,
            "stable_cfl": 1.25,
        },
    )
    solver = create_solver("stable-fluids")
    solver.initialize(scenario, NacaFoil(scenario.foil), 0)
    transverse_probe: list[float] = []
    for step in range(80):
        time = (step + 1) * 0.05
        solver.advance(scenario.control_at(time), 0.05)
        if step >= 30:
            transverse_probe.append(float(solver.export_state().velocity[0, 24, 48, 1]))

    assert np.ptp(np.asarray(transverse_probe)) > 0.15
    assert solver.diagnostics().values["recirculation_area"] > 0.0
