import itertools

import numpy as np
import pytest

from foilbench_py.core.geometry import NacaFoil
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
