from dataclasses import replace

# pyright: reportPrivateUsage=false

import numpy as np
import pytest

from foilbench_py.core.geometry import NacaFoil
from foilbench_py.core.models import CanonicalFlowState, ControlState, NumericalFailure
from foilbench_py.core.protocol import FlowSolver
from foilbench_py.solvers.factory import create_solver, solver_ids
from foilbench_py.solvers.lbm import LBMSolver
from foilbench_py.solvers.pic_flip import PicFlipSolver
from foilbench_py.solvers.stable_fluids import StableFluidsSolver
from tests.helpers import ScenarioFactory


def _assert_same_canonical_state(
    first: CanonicalFlowState,
    second: CanonicalFlowState,
) -> None:
    assert first.schema_version == second.schema_version
    assert first.dimension == second.dimension
    assert first.bounds == second.bounds
    assert first.resolution == second.resolution
    assert first.periodic_axes == second.periodic_axes
    assert first.time == second.time
    assert first.precision == second.precision
    assert first.angle_degrees == second.angle_degrees
    assert first.angular_velocity_degrees == second.angular_velocity_degrees
    np.testing.assert_array_equal(first.velocity, second.velocity)
    if first.density is None:
        assert second.density is None
    else:
        assert second.density is not None
        np.testing.assert_array_equal(first.density, second.density)


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
    assert report.state_revision == 1
    assert sampled.shape == (2, 2)
    assert np.isfinite(sampled).all()
    assert state.velocity.shape == (1, scenario.domain.ny, scenario.domain.nx, 2)
    assert all(np.isfinite(value) for value in diagnostics.values.values())
    assert diagnostics.state_revision == report.state_revision


def test_lbm_distinct_requested_intervals_execute_distinct_physical_updates(
    scenario_factory: ScenarioFactory,
) -> None:
    scenario = scenario_factory(resolution=(32, 16))
    geometry = NacaFoil(scenario.foil)
    shorter = LBMSolver()
    longer = LBMSolver()
    shorter.initialize(scenario, geometry, scenario.seed)
    longer.initialize(scenario, geometry, scenario.seed)

    shorter_report = shorter.advance(ControlState(0.0075, 4.0, 0.0), 0.0075)
    longer_report = longer.advance(ControlState(0.01, 4.0, 0.0), 0.01)

    assert shorter_report.advanced_dt == pytest.approx(0.0075)
    assert longer_report.advanced_dt == pytest.approx(0.01)
    assert not np.array_equal(
        shorter.export_state().velocity,
        longer.export_state().velocity,
    )


@pytest.mark.parametrize("solver_id", solver_ids())
def test_solver_accepts_runtime_reynolds_changes(
    solver_id: str,
    scenario_factory: ScenarioFactory,
) -> None:
    scenario = scenario_factory(resolution=(32, 16))
    solver = create_solver(solver_id)
    solver.initialize(scenario, NacaFoil(scenario.foil), scenario.seed)

    solver.set_reynolds(2.0 * scenario.reynolds)
    solver.advance(scenario.control_at(scenario.output_dt), scenario.output_dt)

    assert solver.reynolds == 2.0 * scenario.reynolds
    assert solver.diagnostics().values["requested_reynolds"] == 2.0 * scenario.reynolds
    assert np.isfinite(solver.export_state().velocity).all()
    with pytest.raises(ValueError, match="Reynolds"):
        solver.set_reynolds(0.0)


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


def test_pic_flip_abrupt_stall_does_not_amplify_transfer_error(
    scenario_factory: ScenarioFactory,
) -> None:
    scenario = scenario_factory(resolution=(160, 96))
    scenario = replace(
        scenario,
        domain=replace(
            scenario.domain,
            bounds=((-1.5, 3.5), (-1.5, 1.5)),
        ),
        reynolds=1000.0,
        output_dt=1.0 / 60.0,
        solver_options={
            **scenario.solver_options,
            "pressure_tolerance": 0.001,
        },
    )
    solver = create_solver("pic-flip")
    solver.initialize(scenario, NacaFoil(scenario.foil), scenario.seed)

    report = None
    for step in range(7):
        time = (step + 1) * scenario.output_dt
        report = solver.advance(ControlState(time, 25.0, 0.0), scenario.output_dt)

    assert report is not None
    diagnostics = solver.diagnostics()
    assert diagnostics.values["kinetic_energy"] < 1.0
    assert diagnostics.values["enstrophy"] < 100.0
    assert np.isfinite(solver.export_state().velocity).all()


def test_pic_flip_periodic_translation_preserves_population(
    scenario_factory: ScenarioFactory,
) -> None:
    scenario = scenario_factory(
        resolution=(40, 20),
        foil_in_domain=False,
        periodic_axes=("x", "y"),
    )
    scenario = replace(
        scenario,
        domain=replace(
            scenario.domain,
            bounds=((0.0, 1.0), (0.0, 0.5)),
        ),
        output_dt=1.0,
        solver_options={
            **scenario.solver_options,
            "pic_population_interval": 100,
        },
    )
    solver = create_solver("pic-flip")
    solver.initialize(scenario, NacaFoil(scenario.foil), scenario.seed)

    solver.advance(ControlState(1.0, 0.0, 0.0), 1.0)

    diagnostics = solver.diagnostics().values
    assert diagnostics["empty_fluid_cell_fraction"] == 0.0
    assert diagnostics["underfilled_fluid_cell_fraction"] == 0.0
    assert diagnostics["max_particles_per_fluid_cell"] == 4.0


def test_pic_flip_large_angle_change_resolves_pose_sweep_with_zero_wall_velocity(
    scenario_factory: ScenarioFactory,
) -> None:
    scenario = scenario_factory(resolution=(80, 40))
    scenario = replace(
        scenario,
        domain=replace(
            scenario.domain,
            bounds=((-1.5, 3.5), (-1.5, 1.5)),
        ),
        output_dt=1.0 / 60.0,
    )
    solver = create_solver("pic-flip")
    solver.initialize(scenario, NacaFoil(scenario.foil), scenario.seed)
    report = solver.advance(
        ControlState(scenario.output_dt, 30.0, 0.0),
        scenario.output_dt,
    )

    diagnostics = solver.diagnostics().values
    assert report.substeps > 1
    assert diagnostics["swept_collisions_last_step"] > 0.0
    assert diagnostics["particles_inside_solid"] == 0.0
    assert solver.export_state().angular_velocity_degrees == 0.0
    assert np.isfinite(solver.export_state().velocity).all()


def test_pic_flip_rejects_an_unsafe_configured_cfl(
    scenario_factory: ScenarioFactory,
) -> None:
    scenario = scenario_factory(resolution=(32, 16))
    scenario = replace(
        scenario,
        solver_options={**scenario.solver_options, "pic_cfl": 1.01},
    )
    solver = create_solver("pic-flip")

    with pytest.raises(ValueError, match="pic_cfl"):
        solver.initialize(scenario, NacaFoil(scenario.foil), scenario.seed)


def test_lbm_rejects_nonfinite_post_step_state(
    scenario_factory: ScenarioFactory,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    scenario = scenario_factory(resolution=(24, 12))
    solver = LBMSolver()
    solver.initialize(scenario, NacaFoil(scenario.foil), scenario.seed)
    before = solver.export_state()
    before_revision = solver.state_revision
    original_step = solver._step

    def nonfinite_step(control: ControlState) -> None:
        original_step(control)
        assert solver._f is not None
        solver._f[0, 0, 0] = np.nan

    monkeypatch.setattr(solver, "_step", nonfinite_step)
    with pytest.raises(NumericalFailure, match="populations") as captured:
        solver.advance(scenario.control_at(scenario.output_dt), scenario.output_dt)

    assert captured.value.reason == "invalid_population"
    assert captured.value.stage == "postcondition"
    assert solver.state_revision == before_revision
    _assert_same_canonical_state(before, solver.export_state())


def test_pic_flip_rejects_nonfinite_post_step_state(
    scenario_factory: ScenarioFactory,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    scenario = scenario_factory(resolution=(24, 12))
    solver = PicFlipSolver()
    solver.initialize(scenario, NacaFoil(scenario.foil), scenario.seed)
    before = solver.export_state()
    before_revision = solver.state_revision

    def nonfinite_projection(
        u: np.ndarray,
        v: np.ndarray,
        control: ControlState,
        dt: float,
    ) -> tuple[np.ndarray, np.ndarray]:
        del control, dt
        return np.full_like(u, np.nan), np.full_like(v, np.nan)

    monkeypatch.setattr(solver, "_project_faces", nonfinite_projection)
    with pytest.raises(NumericalFailure, match="non-finite state") as captured:
        solver.advance(scenario.control_at(scenario.output_dt), scenario.output_dt)

    assert captured.value.reason == "nonfinite_state"
    assert solver.state_revision == before_revision
    _assert_same_canonical_state(before, solver.export_state())


def test_stable_fluids_failed_advance_rolls_back_all_canonical_state(
    scenario_factory: ScenarioFactory,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    scenario = scenario_factory(resolution=(24, 12))
    solver = StableFluidsSolver()
    solver.initialize(scenario, NacaFoil(scenario.foil), scenario.seed)
    before = solver.export_state()
    before_revision = solver.state_revision

    def failed_projection(_dt: float) -> None:
        assert solver._u is not None
        solver._u[0, 0] = 123.0
        raise NumericalFailure("projection_failure", "injected failure", "projection")

    monkeypatch.setattr(solver, "_apply_projection", failed_projection)
    with pytest.raises(NumericalFailure, match="injected failure"):
        solver.advance(scenario.control_at(scenario.output_dt), scenario.output_dt)

    assert solver.state_revision == before_revision
    _assert_same_canonical_state(before, solver.export_state())


@pytest.mark.parametrize("solver_id", solver_ids())
def test_rejected_warm_import_is_transactional(
    solver_id: str,
    scenario_factory: ScenarioFactory,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    scenario = scenario_factory(resolution=(24, 12))
    solver = create_solver(solver_id)
    solver.initialize(scenario, NacaFoil(scenario.foil), scenario.seed)
    before = solver.export_state()
    before_revision = solver.state_revision
    imported = replace(
        before,
        time=0.25,
        angle_degrees=12.0,
        angular_velocity_degrees=0.0,
    )
    control = ControlState(0.25, 12.0, 0.0)

    def fail(*_arguments: object) -> None:
        raise NumericalFailure(
            "projection_failure",
            "injected warm-import failure",
            "canonical-import",
        )

    if solver_id == "stable-fluids":
        monkeypatch.setattr(solver, "_apply_projection", fail)
    elif solver_id == "lbm-d2q9":
        monkeypatch.setattr(solver, "_equilibrium", fail)
    else:
        monkeypatch.setattr(solver, "_seed_particles", fail)

    outcome = solver.import_state(imported, control)

    assert not outcome.accepted
    assert outcome.reason == "projection_failure"
    assert outcome.stage == "canonical-import"
    assert solver.state_revision == before_revision
    _assert_same_canonical_state(before, solver.export_state())
