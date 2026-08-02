import numpy as np
import pytest

from foilbench_py.core.models import NumericalFailure
from foilbench_py.core.tracers import TracerSystem
from foilbench_py.viewer.app import ViewerModel
from foilbench_py.viewer.worker import SimulationWorker
from tests.helpers import ScenarioFactory


def test_worker_publishes_detached_snapshots_and_processes_paused_commands(
    scenario_factory: ScenarioFactory,
) -> None:
    model = ViewerModel.create(scenario_factory(resolution=(32, 16)))
    worker = SimulationWorker(model, maximum_steps_per_second=120.0)
    worker.start()
    try:
        running = worker.wait_for_revision(2)
        assert running.simulation_time > 0.0
        assert not running.positions.flags.writeable
        assert not running.path_segments.flags.writeable
        if running.vorticity is not None:
            assert not running.vorticity.flags.writeable

        paused = worker.wait_for_command(worker.toggle_pause())
        paused_time = paused.simulation_time
        angled = worker.wait_for_command(worker.set_angle(18.0))
        assert angled.simulation_time == paused_time
        assert angled.angle_degrees == 18.0

        faster = worker.wait_for_command(worker.adjust_reynolds(0.25))
        assert faster.simulation_time == paused_time
        assert "rate=" in faster.status

        tuned = worker.wait_for_command(worker.adjust_tuning(0.05))
        assert tuned.simulation_time == paused_time
        assert "adv=skew-rk2" in tuned.status

        cropped = worker.wait_for_command(worker.toggle_crop())
        assert cropped.simulation_time == paused_time
        assert cropped.crop_enabled == model.crop_enabled

        switched = worker.wait_for_command(worker.switch_solver("pic-flip"))
        assert switched.simulation_time > paused_time
        assert switched.simulation_time == pytest.approx(
            paused_time + model.scenario.output_dt * model.playback_rate
        )
        assert "PIC/FLIP" in switched.status
        assert np.isfinite(switched.positions).all()
    finally:
        worker.close()
    assert not worker.is_alive


def test_worker_coalesces_drag_commands_and_resets(
    scenario_factory: ScenarioFactory,
) -> None:
    model = ViewerModel.create(scenario_factory(resolution=(32, 16)))
    scenario = model.scenario
    model.paused = True
    worker = SimulationWorker(model)
    first = worker.set_angle(5.0)
    second = worker.set_angle(12.0)
    third = worker.set_angle(24.0)
    assert first < second < third
    worker.start()
    try:
        dragged = worker.wait_for_command(third)
        assert dragged.angle_degrees == 24.0
        reset = worker.wait_for_command(worker.reset())
        assert reset.simulation_time <= scenario.output_dt
        assert np.isclose(
            reset.angle_degrees,
            scenario.control_at(reset.simulation_time).angle_degrees,
        )
    finally:
        worker.close()


def test_worker_recovers_once_from_failed_advance(
    scenario_factory: ScenarioFactory,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    model = ViewerModel.create(
        scenario_factory(resolution=(32, 16)),
        "stable-fluids",
    )
    model.switch_solver("pic-flip")
    model.set_angle(-30.0)
    original_update = ViewerModel.update
    calls = 0

    def fail_once(viewer: ViewerModel, dt: float) -> None:
        nonlocal calls
        calls += 1
        if calls == 1:
            raise NumericalFailure("nonfinite_state", "warm state diverged")
        original_update(viewer, dt)

    monkeypatch.setattr(ViewerModel, "update", fail_once)
    worker = SimulationWorker(model, maximum_steps_per_second=120.0)
    worker.start()
    try:
        recovered = worker.wait_for_revision(1)
        assert recovered.failure is None
        assert recovered.angle_degrees == -30.0
        assert "recovered=fresh restart reason=nonfinite_state" in recovered.status

        resumed = worker.wait_for_revision(2)
        assert resumed.failure is None
        assert resumed.simulation_time > 0.0
    finally:
        worker.close()


def test_worker_resets_modified_reynolds_after_consecutive_failures(
    scenario_factory: ScenarioFactory,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    model = ViewerModel.create(
        scenario_factory(resolution=(32, 16)),
        "stable-fluids",
    )
    scenario_reynolds = model.scenario.reynolds
    model.set_reynolds(10.0 * scenario_reynolds)
    original_update = ViewerModel.update
    calls = 0

    def fail_twice(viewer: ViewerModel, dt: float) -> None:
        nonlocal calls
        calls += 1
        if calls <= 2:
            raise NumericalFailure("nonfinite_state", "runtime Reynolds diverged")
        original_update(viewer, dt)

    monkeypatch.setattr(ViewerModel, "update", fail_twice)
    worker = SimulationWorker(model, maximum_steps_per_second=10.0)
    worker.start()
    try:
        first_recovery = worker.wait_for_revision(1)
        assert first_recovery.failure is None
        assert "Re reset" not in first_recovery.status

        circuit_break = worker.wait_for_revision(2)
        assert circuit_break.failure is None
        assert model.manager.reynolds == scenario_reynolds
        assert f"Re reset {10.0 * scenario_reynolds:.0f}->{scenario_reynolds:.0f}" in (
            circuit_break.status
        )

        resumed = worker.wait_for_revision(3)
        assert resumed.failure is None
        assert resumed.simulation_time > 0.0
    finally:
        worker.close()


def test_worker_resets_modified_reynolds_after_failures_in_window(
    scenario_factory: ScenarioFactory,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    model = ViewerModel.create(
        scenario_factory(resolution=(32, 16)),
        "stable-fluids",
    )
    scenario_reynolds = model.scenario.reynolds
    model.set_reynolds(10.0 * scenario_reynolds)
    original_update = ViewerModel.update
    calls = 0

    def fail_interleaved(viewer: ViewerModel, dt: float) -> None:
        nonlocal calls
        calls += 1
        if calls in {1, 3, 5}:
            raise NumericalFailure("nonfinite_state", "intermittent Reynolds instability")
        original_update(viewer, dt)

    monkeypatch.setattr(ViewerModel, "update", fail_interleaved)
    worker = SimulationWorker(model, maximum_steps_per_second=10.0)
    worker.start()
    try:
        circuit_break = worker.wait_for_revision(5)
        assert circuit_break.failure is None
        assert model.manager.reynolds == scenario_reynolds
        assert "Re reset" in circuit_break.status

        resumed = worker.wait_for_revision(6)
        assert resumed.failure is None
        assert resumed.simulation_time > 0.0
    finally:
        worker.close()


def test_worker_preserves_pose_barriers_and_acknowledges_shutdown(
    scenario_factory: ScenarioFactory,
) -> None:
    model = ViewerModel.create(scenario_factory(resolution=(32, 16)), "stable-fluids")
    model.paused = True
    worker = SimulationWorker(model)
    worker.set_angle(10.0, 1.0)
    worker.switch_solver("pic-flip")
    final_pose = worker.set_angle(20.0, 2.0)
    worker.start()
    applied = worker.wait_for_command(final_pose)

    assert applied.angle_degrees == 20.0
    assert model.manager.solver.export_state().angle_degrees == 10.0

    worker.close()
    shutdown = worker.latest_snapshot()
    assert shutdown.applied_command > final_pose
    assert not worker.is_alive
    with pytest.raises(RuntimeError, match="closed"):
        worker.set_angle(0.0)


def test_worker_pauses_after_intermittent_failures_at_scenario_reynolds(
    scenario_factory: ScenarioFactory,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    model = ViewerModel.create(
        scenario_factory(resolution=(32, 16)),
        "stable-fluids",
    )
    original_update = ViewerModel.update
    calls = 0

    def fail_interleaved(viewer: ViewerModel, dt: float) -> None:
        nonlocal calls
        calls += 1
        if calls in {1, 3, 5}:
            raise NumericalFailure("nonfinite_state", "intermittent baseline instability")
        original_update(viewer, dt)

    monkeypatch.setattr(ViewerModel, "update", fail_interleaved)
    worker = SimulationWorker(model, maximum_steps_per_second=20.0)
    worker.start()
    try:
        stopped = worker.wait_for_revision(5)
        assert model.paused
        assert stopped.failure is not None
        paused_time = stopped.simulation_time
        assert worker.latest_snapshot().simulation_time == paused_time
    finally:
        worker.close()


def test_tracer_programming_error_pauses_without_solver_recovery(
    scenario_factory: ScenarioFactory,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    model = ViewerModel.create(
        scenario_factory(resolution=(32, 16)),
        "stable-fluids",
    )
    solver = model.manager.solver

    def fail_tracers(
        tracers: TracerSystem,
        solver: object,
        control: object,
        dt: float,
    ) -> None:
        del tracers, solver, control, dt
        raise ValueError("injected tracer invariant failure")

    monkeypatch.setattr(TracerSystem, "update", fail_tracers)
    worker = SimulationWorker(model, maximum_steps_per_second=20.0)
    worker.start()
    try:
        failed = worker.wait_for_revision(1)
        assert model.paused
        assert failed.failure is not None
        assert "injected tracer invariant failure" in failed.failure
        assert model.manager.solver is solver
        assert model.recovery_count == 0
    finally:
        worker.close()


def test_worker_uses_pose_only_mode_after_consecutive_rapid_drag_failures(
    scenario_factory: ScenarioFactory,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    model = ViewerModel.create(
        scenario_factory(resolution=(32, 16)),
        "stable-fluids",
    )
    model.set_angle(30.0)
    original_update = ViewerModel.update
    calls = 0

    def fail_twice_during_rapid_drag(viewer: ViewerModel, dt: float) -> None:
        nonlocal calls
        calls += 1
        viewer.last_requested_angular_velocity_degrees = 600.0
        if calls <= 2 or viewer.pose_only_guarded_trial:
            raise NumericalFailure("excessive_velocity", "moving wall diverged")
        original_update(viewer, dt)

    monkeypatch.setattr(ViewerModel, "update", fail_twice_during_rapid_drag)
    worker = SimulationWorker(model, maximum_steps_per_second=10.0)
    worker.start()
    try:
        first_recovery = worker.wait_for_revision(1)
        assert first_recovery.failure is None
        assert "motion=pose-only" not in first_recovery.status

        pose_only = worker.wait_for_revision(2)
        assert pose_only.failure is None
        assert pose_only.angle_degrees == 30.0
        assert "motion=pose-only" in pose_only.status
        assert model.manager.solver.export_state().angular_velocity_degrees == 0.0

        released = worker.wait_for_command(worker.release_angle())
        assert released.failure is None
        assert "motion=pose-only" not in released.status
        assert not model.pose_only_drag

        guarded_failure = worker.wait_for_revision(released.revision + 1)
        assert guarded_failure.failure is not None
        assert model.paused
    finally:
        worker.close()


def test_worker_advances_stable_fluids_with_capped_rapid_drag(
    scenario_factory: ScenarioFactory,
) -> None:
    scenario = scenario_factory(resolution=(64, 32))
    scenario.solver_options["stable_advection"] = "skew-rk2"
    model = ViewerModel.create(scenario, "stable-fluids")
    model.set_angle(-30.0)
    worker = SimulationWorker(model, maximum_steps_per_second=10.0)
    worker.start()
    try:
        advanced = worker.wait_for_revision(2, timeout=10.0)
        assert advanced.failure is None
        assert advanced.angle_degrees == -30.0
        assert np.isfinite(advanced.positions).all()
        assert advanced.simulation_time > 0.0
    finally:
        worker.close()


def test_unpaused_switch_publishes_the_validation_boundary(
    scenario_factory: ScenarioFactory,
) -> None:
    model = ViewerModel.create(scenario_factory(resolution=(24, 12)))
    worker = SimulationWorker(model, maximum_steps_per_second=5.0)
    switch_sequence = worker.switch_solver("pic-flip")
    worker.start()
    try:
        switched = worker.wait_for_command(switch_sequence)
        assert switched.simulation_time == pytest.approx(model.scenario.output_dt)
        assert "PIC/FLIP" in switched.status
    finally:
        worker.close()
