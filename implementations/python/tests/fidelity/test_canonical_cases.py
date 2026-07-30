from dataclasses import replace
from typing import cast

import numpy as np
import pytest

from foilbench_py.core.geometry import NacaFoil
from foilbench_py.core.models import ControlKeyframe, DomainSpec
from foilbench_py.solvers.factory import create_solver, solver_ids
from tests.helpers import ScenarioFactory


@pytest.mark.fidelity
@pytest.mark.parametrize("solver_id", solver_ids())
def test_uniform_flow_remains_finite_and_near_uniform(
    solver_id: str, scenario_factory: ScenarioFactory
) -> None:
    scenario = scenario_factory(
        resolution=(40, 20),
        foil_in_domain=False,
        periodic_axes=("x", "y"),
    )
    geometry = NacaFoil(scenario.foil)
    solver = create_solver(solver_id)
    solver.initialize(scenario, geometry, 0)
    solver.advance(scenario.control_at(0.01), 0.01)
    velocity = solver.export_state().velocity[0]
    assert np.isfinite(velocity).all()
    mean_x = float(cast(np.floating, np.mean(velocity[:, :, 0])))
    mean_y = float(cast(np.floating, np.mean(np.abs(velocity[:, :, 1]))))
    assert mean_x == pytest.approx(1.0, abs=0.15)
    assert mean_y < 0.15


@pytest.mark.fidelity
@pytest.mark.parametrize("solver_id", solver_ids())
def test_taylor_green_energy_is_finite(solver_id: str, scenario_factory: ScenarioFactory) -> None:
    scenario = scenario_factory(
        resolution=(40, 20),
        initial_condition="taylor-green",
        foil_in_domain=False,
        periodic_axes=("x", "y"),
    )
    solver = create_solver(solver_id)
    solver.initialize(scenario, NacaFoil(scenario.foil), 0)
    before = solver.diagnostics().values["kinetic_energy"]
    solver.advance(scenario.control_at(0.005), 0.005)
    after = solver.diagnostics().values["kinetic_energy"]
    assert np.isfinite(after)
    assert 0.0 <= after < max(before * 1.25, 1.0e-8)


@pytest.mark.fidelity
@pytest.mark.parametrize("solver_id", solver_ids())
def test_poiseuille_profile_has_faster_center(
    solver_id: str, scenario_factory: ScenarioFactory
) -> None:
    scenario = scenario_factory(
        resolution=(40, 20),
        initial_condition="poiseuille",
        foil_in_domain=False,
        periodic_axes=("x",),
    )
    solver = create_solver(solver_id)
    solver.initialize(scenario, NacaFoil(scenario.foil), 0)
    velocity = solver.export_state().velocity[0, :, :, 0]
    center = float(np.mean(velocity[velocity.shape[0] // 2]))
    lower = float(cast(np.floating, np.mean(velocity[0])))
    upper = float(cast(np.floating, np.mean(velocity[-1])))
    walls = 0.5 * (lower + upper)
    assert center > walls


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
