import numpy as np
import pytest

from foilbench_py.core.geometry import NacaFoil
from foilbench_py.core.protocol import FlowSolver
from foilbench_py.solvers.factory import create_solver, solver_ids
from tests.helpers import ScenarioFactory


@pytest.mark.parametrize("solver_id", solver_ids())
def test_solver_protocol_and_canonical_export(
    solver_id: str, scenario_factory: ScenarioFactory
) -> None:
    scenario = scenario_factory()
    geometry = NacaFoil(scenario.foil)
    solver = create_solver(solver_id)
    assert isinstance(solver, FlowSolver)
    solver.initialize(scenario, geometry, scenario.seed)
    report = solver.advance(scenario.control_at(0.01), 0.01)
    points = np.asarray([[-1.0, 0.0], [1.0, 0.5]], dtype=np.float32)
    sampled = solver.sample_velocity(points)
    state = solver.export_state()
    diagnostics = solver.diagnostics()
    assert report.advanced_dt == pytest.approx(0.01)
    assert report.substeps >= 1
    assert sampled.shape == (2, 2)
    assert np.isfinite(sampled).all()
    assert state.velocity.shape == (1, scenario.domain.ny, scenario.domain.nx, 2)
    assert all(np.isfinite(value) for value in diagnostics.values.values())


@pytest.mark.parametrize("solver_id", solver_ids())
def test_solver_is_deterministic_for_fixed_seed(
    solver_id: str, scenario_factory: ScenarioFactory
) -> None:
    scenario = scenario_factory(resolution=(32, 16))
    geometry = NacaFoil(scenario.foil)
    first = create_solver(solver_id)
    second = create_solver(solver_id)
    first.initialize(scenario, geometry, 17)
    second.initialize(scenario, geometry, 17)
    control = scenario.control_at(0.01)
    first.advance(control, 0.01)
    second.advance(control, 0.01)
    np.testing.assert_allclose(
        first.export_state().velocity,
        second.export_state().velocity,
        rtol=1.0e-6,
        atol=1.0e-6,
    )


def test_lbm_open_boundaries_remain_finite_for_many_steps(
    scenario_factory: ScenarioFactory,
) -> None:
    scenario = scenario_factory(
        resolution=(48, 24),
        foil_in_domain=False,
    )
    solver = create_solver("lbm-d2q9")
    solver.initialize(scenario, NacaFoil(scenario.foil), 0)
    report = None
    for step in range(300):
        time = (step + 1) * scenario.output_dt
        report = solver.advance(scenario.control_at(time), scenario.output_dt)

    assert report is not None
    assert any("effective Re" in warning for warning in report.warnings)
    state = solver.export_state()
    np.testing.assert_allclose(
        np.mean(state.velocity[0, :, :, 0]),
        scenario.freestream[0],
        rtol=0.01,
    )
    assert np.isfinite(state.velocity).all()
