import itertools

import numpy as np
import pytest

from foilbench_py.core.geometry import NacaFoil
from foilbench_py.core.models import ControlState, NumericalFailure
from foilbench_py.core.switching import SolverManager
from foilbench_py.solvers.factory import create_solver, solver_ids
from foilbench_py.solvers.lbm import LBMSolver
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
    validation_control = ControlState(0.02, angle, 0.0)
    outcome = manager.switch(destination, control, validation_control, 0.01)
    assert outcome.accepted
    assert outcome.report is not None
    report = outcome.report
    state = manager.solver.export_state()
    assert report.source_solver == source
    assert report.destination_solver == destination
    assert manager.solver.info.id == destination
    assert state.time == pytest.approx(0.02)
    assert np.isfinite(state.velocity).all()


def test_stable_to_lbm_warm_switch_stays_finite(
    scenario_factory: ScenarioFactory,
) -> None:
    scenario = scenario_factory(resolution=(48, 24))
    geometry = NacaFoil(scenario.foil)
    manager = SolverManager(create_solver, scenario, geometry, "stable-fluids")
    manager.solver.advance(scenario.control_at(0.01), scenario.output_dt)
    manager.switch(
        "lbm-d2q9",
        scenario.control_at(0.01),
        scenario.control_at(0.01 + scenario.output_dt),
        scenario.output_dt,
    )

    for step in range(200):
        time = 0.01 + (step + 2) * scenario.output_dt
        manager.solver.advance(scenario.control_at(time), scenario.output_dt)

    state = manager.solver.export_state()
    assert np.isfinite(state.velocity).all()


def test_warm_import_rejection_is_structured_and_retains_source(
    scenario_factory: ScenarioFactory,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    scenario = scenario_factory(resolution=(32, 16))
    manager = SolverManager(
        create_solver,
        scenario,
        NacaFoil(scenario.foil),
        "stable-fluids",
    )

    def reject_import(
        _solver: LBMSolver,
        _state: object,
        _control: ControlState,
    ) -> object:
        raise ValueError("warm import requires the same 2D resolution")

    monkeypatch.setattr(LBMSolver, "import_state", reject_import)
    outcome = manager.switch(
        "lbm-d2q9",
        scenario.control_at(0.0),
        scenario.control_at(scenario.output_dt),
        scenario.output_dt,
    )

    assert not outcome.accepted
    assert outcome.reason == "incompatible_domain"
    assert manager.solver.info.id == "stable-fluids"


def test_warm_validation_failure_is_structured_and_retains_source(
    scenario_factory: ScenarioFactory,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    scenario = scenario_factory(resolution=(32, 16))
    manager = SolverManager(
        create_solver,
        scenario,
        NacaFoil(scenario.foil),
        "stable-fluids",
    )
    source = manager.solver
    source_time = source.export_state().time

    def reject_validation(
        _solver: LBMSolver,
        _control: ControlState,
        _target_dt: float,
    ) -> object:
        raise NumericalFailure("projection_failure", "injected first-step rejection")

    monkeypatch.setattr(LBMSolver, "advance", reject_validation)
    outcome = manager.switch(
        "lbm-d2q9",
        scenario.control_at(0.0),
        scenario.control_at(scenario.output_dt),
        scenario.output_dt,
    )

    assert not outcome.accepted
    assert outcome.reason == "projection_failure"
    assert manager.solver is source
    assert manager.solver.export_state().time == source_time


def test_runtime_reynolds_survives_warm_switch_and_restart(
    scenario_factory: ScenarioFactory,
) -> None:
    scenario = scenario_factory(resolution=(32, 16))
    manager = SolverManager(
        create_solver,
        scenario,
        NacaFoil(scenario.foil),
        "stable-fluids",
    )
    selected_reynolds = 4.0 * scenario.reynolds
    manager.set_reynolds(selected_reynolds)

    manager.switch(
        "pic-flip",
        scenario.control_at(0.0),
        scenario.control_at(scenario.output_dt),
        scenario.output_dt,
    )
    assert manager.reynolds == selected_reynolds
    assert manager.solver.reynolds == selected_reynolds

    manager.restart_at(ControlState(0.0, 12.0, 0.0))
    assert manager.reynolds == selected_reynolds
    assert manager.solver.reynolds == selected_reynolds


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
    manager.switch(
        destination,
        ControlState(0.01, 25.0, 0.0),
        ControlState(0.02, 25.0, 0.0),
        0.01,
    )

    manager.restart_at(ControlState(0.0, -29.0, 0.0))

    state = manager.solver.export_state()
    assert manager.solver.info.id == destination
    assert manager.last_import is None
    assert state.time == 0.0
    assert state.angle_degrees == -29.0
    assert np.isfinite(state.velocity).all()
