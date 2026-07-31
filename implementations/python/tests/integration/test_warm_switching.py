import itertools

import numpy as np
import pytest

from foilbench_py.core.geometry import NacaFoil
from foilbench_py.core.models import ControlState
from foilbench_py.core.switching import SolverManager
from foilbench_py.solvers.factory import create_solver, solver_ids
from tests.helpers import ScenarioFactory


@pytest.mark.parametrize(
    ("source", "destination"),
    list(itertools.permutations(solver_ids(), 2)),
)
@pytest.mark.parametrize("angle", [4.0, 25.0])
def test_all_directed_warm_switches(
    source: str,
    destination: str,
    angle: float,
    scenario_factory: ScenarioFactory,
) -> None:
    scenario = scenario_factory(resolution=(32, 16))
    geometry = NacaFoil(scenario.foil)
    manager = SolverManager(create_solver, scenario, geometry, source)
    control = type(scenario.control_at(0.01))(0.01, angle, 0.0)
    manager.solver.advance(control, 0.01)
    report = manager.switch(destination, control)
    state = manager.solver.export_state()
    assert report.source_solver == source
    assert report.destination_solver == destination
    assert manager.solver.info.id == destination
    assert state.time == pytest.approx(0.01)
    assert np.isfinite(state.velocity).all()


def test_stable_to_lbm_warm_switch_stays_finite(
    scenario_factory: ScenarioFactory,
) -> None:
    scenario = scenario_factory(resolution=(48, 24))
    geometry = NacaFoil(scenario.foil)
    manager = SolverManager(create_solver, scenario, geometry, "stable-fluids")
    manager.solver.advance(scenario.control_at(0.01), scenario.output_dt)
    manager.switch("lbm-d2q9", scenario.control_at(0.01))

    for step in range(200):
        time = 0.01 + (step + 1) * scenario.output_dt
        manager.solver.advance(scenario.control_at(time), scenario.output_dt)

    state = manager.solver.export_state()
    assert np.isfinite(state.velocity).all()


@pytest.mark.parametrize("destination", solver_ids())
def test_restart_discards_imported_state_and_starts_at_current_angle(
    destination: str,
    scenario_factory: ScenarioFactory,
) -> None:
    scenario = scenario_factory(resolution=(32, 16))
    geometry = NacaFoil(scenario.foil)
    source = next(solver_id for solver_id in solver_ids() if solver_id != destination)
    manager = SolverManager(create_solver, scenario, geometry, source)
    manager.solver.advance(scenario.control_at(0.01), scenario.output_dt)
    manager.switch(destination, ControlState(0.01, 25.0, 0.0))

    manager.restart_at(ControlState(0.0, -29.0, 0.0))

    state = manager.solver.export_state()
    assert manager.solver.info.id == destination
    assert manager.last_import is None
    assert state.time == 0.0
    assert state.angle_degrees == -29.0
    assert np.isfinite(state.velocity).all()
