from dataclasses import replace

import numpy as np
import pytest

from foilbench_py.solvers.pic_flip import PicFlipSolver
from foilbench_py.viewer.app import (
    ViewerModel,
    viewer_bounds,
    viewer_crop_enabled_by_default,
)
from tests.helpers import ScenarioFactory


def test_viewer_bounds_can_crop_only_the_presentation(
    scenario_factory: ScenarioFactory,
) -> None:
    scenario = scenario_factory(resolution=(32, 16))
    scenario.solver_options["viewer_crop_cells"] = 3

    bounds = viewer_bounds(scenario)
    full_bounds = viewer_bounds(scenario, cropped=False)

    assert bounds[0][0] == pytest.approx(scenario.domain.bounds[0][0] + 3 * scenario.domain.dx)
    assert bounds[0][1] == pytest.approx(scenario.domain.bounds[0][1] - 3 * scenario.domain.dx)
    assert bounds[1][0] == pytest.approx(scenario.domain.bounds[1][0] + 3 * scenario.domain.dy)
    assert bounds[1][1] == pytest.approx(scenario.domain.bounds[1][1] - 3 * scenario.domain.dy)
    assert scenario.domain.bounds != bounds
    assert full_bounds == (scenario.domain.bounds[0], scenario.domain.bounds[1])
    assert viewer_crop_enabled_by_default(scenario)

    scenario.solver_options["viewer_crop_default"] = False
    assert not viewer_crop_enabled_by_default(scenario)
    scenario.solver_options["viewer_crop_default"] = "yes"
    with pytest.raises(TypeError, match="must be a boolean"):
        viewer_crop_enabled_by_default(scenario)


def test_headless_viewer_update_and_switch(
    scenario_factory: ScenarioFactory,
) -> None:
    scenario = scenario_factory(resolution=(32, 16))
    model = ViewerModel.create(scenario, "stable-fluids")
    before = model.tracers.positions.copy()
    model.update(0.5)
    assert model.time == 0.01
    assert not np.array_equal(before, model.tracers.positions)
    assert model.vorticity_display is not None
    assert model.vorticity_display.shape == (16, 32)
    assert model.solver_steps_per_second > 0.0
    assert "step=" in model.status()
    assert "AoA=" in model.status()
    history = model.tracers.history.copy()
    model.switch_solver("lbm-d2q9")
    assert model.manager.solver.info.id == "lbm-d2q9"
    np.testing.assert_array_equal(history, model.tracers.history)
    assert "warm-import transient" in model.status()


def test_runtime_reynolds_mildly_scales_physical_playback(
    scenario_factory: ScenarioFactory,
) -> None:
    scenario = scenario_factory(resolution=(32, 16))
    model = ViewerModel.create(scenario, "stable-fluids")

    model.adjust_reynolds(1.0)
    model.update(scenario.output_dt)

    assert model.manager.reynolds == 10.0 * scenario.reynolds
    assert model.playback_rate == pytest.approx(1.5)
    assert model.time == pytest.approx(1.5 * scenario.output_dt)
    assert "rate=1.50x" in model.status()
    model.switch_solver("lbm-d2q9")
    assert model.manager.reynolds == 10.0 * scenario.reynolds
    model.reset_reynolds()
    assert model.manager.reynolds == scenario.reynolds


def test_manual_pose_cancels_schedule_while_solver_and_reynolds_changes_do_not(
    scenario_factory: ScenarioFactory,
) -> None:
    scenario = scenario_factory(resolution=(32, 16))
    model = ViewerModel.create(scenario, "stable-fluids")

    model.adjust_reynolds(0.25)
    assert model.angle_override is None
    assert model.switch_solver("lbm-d2q9").accepted
    assert model.angle_override is None

    model.set_angle(18.0, 1.0)
    assert model.angle_override == 18.0
    model.recover_solver(FloatingPointError("non-finite injected state"))
    assert model.angle_override == 18.0
    model.reset()
    assert model.angle_override is None


def test_hidden_vorticity_stops_field_refresh_until_reenabled(
    scenario_factory: ScenarioFactory,
) -> None:
    model = ViewerModel.create(scenario_factory(resolution=(32, 16)), "stable-fluids")
    model.presentation.diagnostic_interval = 0.001
    assert not model.toggle_vorticity()
    hidden_revision = model.vorticity_revision

    model.update(model.scenario.output_dt)

    assert model.vorticity_revision == hidden_revision
    assert model.toggle_vorticity()
    assert model.vorticity_revision == hidden_revision + 1


def test_solver_tuning_is_context_sensitive_and_stable_mode_persists(
    scenario_factory: ScenarioFactory,
) -> None:
    scenario = scenario_factory(resolution=(32, 16))
    model = ViewerModel.create(scenario, "stable-fluids")
    initial_velocity = model.manager.solver.export_state().velocity.copy()

    assert "adv=maccormack" in model.status()
    assert model.adjust_solver_tuning(0.05)
    assert "adv=skew-rk2" in model.status()
    np.testing.assert_array_equal(
        model.manager.solver.export_state().velocity,
        initial_velocity,
    )

    model.switch_solver("lbm-d2q9")
    assert not model.adjust_solver_tuning(0.05)
    assert "tune=no adjustable tuning" in model.status()
    model.switch_solver("stable-fluids")
    assert "adv=skew-rk2" in model.status()

    model.recover_solver(FloatingPointError("injected failure"))
    assert "adv=skew-rk2" in model.status()
    assert model.adjust_solver_tuning(-0.05)
    assert "adv=maccormack" in model.status()

    model.switch_solver("pic-flip")
    pic_solver = model.manager.solver
    assert isinstance(pic_solver, PicFlipSolver)
    before_blend = pic_solver.blend
    assert model.adjust_solver_tuning(-0.05)
    assert pic_solver.blend == pytest.approx(before_blend - 0.05)

    model.switch_solver("stable-fluids")
    assert model.adjust_solver_tuning(0.05)
    model.reset()
    assert "adv=maccormack" in model.status()


def test_pose_only_drag_tracks_angle_and_clears_after_release(
    scenario_factory: ScenarioFactory,
) -> None:
    scenario = scenario_factory(resolution=(32, 16))
    model = ViewerModel.create(scenario, "stable-fluids")
    model.set_angle(12.0, 1.0)

    assert model.control(scenario.output_dt).angular_velocity_degrees == 0.0
    model.set_angle(13.0, 1.1)
    assert model.control(scenario.output_dt).angular_velocity_degrees != 0.0
    model.enable_pose_only_drag()
    pose_control = model.control(scenario.output_dt)
    assert pose_control.angle_degrees == 13.0
    assert pose_control.angular_velocity_degrees == 0.0
    assert "motion=pose-only" in model.status()

    model.release_angle()
    assert model.pose_only_drag
    assert model.pose_only_release_pending
    model.update(scenario.output_dt)

    assert not model.drag_active
    assert not model.pose_only_drag
    assert not model.pose_only_release_pending
    assert "motion=pose-only" not in model.status()

    model.set_angle(90.0, 2.0)
    assert model.angle_override == 30.0
    model.set_angle(-90.0, 3.0)
    assert model.angle_override == -30.0


def test_pose_only_drag_clears_after_sustained_slow_motion(
    scenario_factory: ScenarioFactory,
) -> None:
    scenario = scenario_factory(resolution=(32, 16))
    model = ViewerModel.create(scenario, "stable-fluids")
    model.set_angle(12.0, 1.0)
    model.set_angle(20.0, 1.01)
    model.enable_pose_only_drag()
    model.update(scenario.output_dt)
    assert model.pose_only_drag

    model.set_angle(20.1, 1.1)
    model.update(scenario.output_dt)
    assert model.pose_only_drag
    assert model.pose_only_calm_steps == 1

    model.set_angle(20.2, 1.2)
    model.update(scenario.output_dt)
    assert model.drag_active
    assert not model.pose_only_drag
    assert model.pose_only_calm_steps == 0


def test_drag_velocity_uses_timestamped_samples_and_a_generous_cap(
    scenario_factory: ScenarioFactory,
) -> None:
    scenario = scenario_factory(resolution=(64, 32))
    scenario.solver_options["stable_advection"] = "skew-rk2"
    model = ViewerModel.create(scenario, "stable-fluids")
    model.set_angle(-30.0, 1.0)

    assert not model.rapid_drag_attempted
    model.set_angle(30.0, 1.01)
    assert model.rapid_drag_attempted
    assert model.requested_tip_speed_ratio <= 8.0 + 1.0e-6


def test_pose_only_survives_warm_switch_and_pic_uses_authoritative_wall_speed(
    scenario_factory: ScenarioFactory,
) -> None:
    scenario = scenario_factory(resolution=(32, 16))
    model = ViewerModel.create(scenario, "stable-fluids")
    model.set_angle(0.0, 1.0)
    model.set_angle(30.0, 1.01)
    model.enable_pose_only_drag()

    assert model.switch_solver("pic-flip").accepted
    assert model.pose_only_drag
    control = model.control(scenario.output_dt)
    assert control.angular_velocity_degrees == 0.0
    model.manager.solver.advance(control, scenario.output_dt)

    assert model.manager.solver.export_state().angular_velocity_degrees == 0.0


def test_display_tracers_expire_without_leaving_empty_regions(
    scenario_factory: ScenarioFactory,
) -> None:
    scenario = scenario_factory(resolution=(32, 16))
    model = ViewerModel.create(scenario, "stable-fluids")
    before = model.tracers.positions.copy()
    generations = model.tracers.generations.copy()
    model.tracers.ages[:] = model.tracers.lifetimes + 1.0

    model.update(0.01)

    assert model.tracers.mode == "display"
    assert np.all(model.tracers.ages == 0.0)
    assert not np.array_equal(before, model.tracers.positions)
    np.testing.assert_array_equal(model.tracers.generations, generations + 1)
    for history_slice in model.tracers.history:
        np.testing.assert_array_equal(history_slice, model.tracers.positions)
    assert model.toggle_tracer_mode() == "material"


def test_tracer_paths_use_explicit_continuity_generations(
    scenario_factory: ScenarioFactory,
) -> None:
    model = ViewerModel.create(scenario_factory(resolution=(32, 16)), "stable-fluids")
    tracers = model.tracers
    full_vertex_count = 2 * tracers.positions.shape[0] * (tracers.history.shape[0] - 1)
    assert tracers.path_segments().shape == (full_vertex_count, 2)

    chronological = np.roll(
        tracers.history_generations,
        -tracers.history_index - 1,
        axis=0,
    )
    chronological[-1, 0] += 1
    tracers.history_generations[:] = np.roll(
        chronological,
        tracers.history_index + 1,
        axis=0,
    )

    assert tracers.path_segments().shape == (full_vertex_count - 2, 2)


def test_periodic_tracer_wrap_preserves_lifetime_and_cuts_path(
    scenario_factory: ScenarioFactory,
) -> None:
    scenario = scenario_factory(resolution=(32, 16))
    scenario = replace(
        scenario,
        domain=replace(scenario.domain, periodic_axes=("x", "y")),
    )
    model = ViewerModel.create(scenario, "stable-fluids")
    tracers = model.tracers
    tracer = 0
    x0, x1 = scenario.domain.bounds[0]
    y0, y1 = scenario.domain.bounds[1]
    timestep = scenario.output_dt
    tracers.positions[:] = (x0 + 0.5, y1 - scenario.domain.dy)
    tracers.positions[tracer] = (x1 - 0.25 * timestep, 0.5 * (y0 + y1))
    tracers.history[:] = tracers.positions[None, :, :]
    tracers.ages[:] = 0.0
    tracers.lifetimes[:] = 100.0
    age = float(tracers.ages[tracer])
    lifetime = float(tracers.lifetimes[tracer])
    generation = int(tracers.generations[tracer])
    full_vertex_count = (tracers.history.shape[0] - 1) * tracers.positions.shape[0] * 2

    tracers.update(model.manager.solver, scenario.control_at(timestep), timestep)

    assert x0 <= tracers.positions[tracer, 0] < x0 + timestep
    assert tracers.ages[tracer] == pytest.approx(age + timestep)
    assert tracers.lifetimes[tracer] == lifetime
    assert tracers.generations[tracer] == generation + 1
    assert tracers.path_segments().shape == (full_vertex_count - 2, 2)


def test_failed_warm_state_recovers_fresh_and_reseeds_tracers(
    scenario_factory: ScenarioFactory,
) -> None:
    model = ViewerModel.create(
        scenario_factory(resolution=(32, 16)),
        "stable-fluids",
    )
    model.update(model.scenario.output_dt)
    recovery_time = model.time
    model.switch_solver("pic-flip")
    model.set_angle(30.0)
    positions = model.tracers.positions.copy()
    history = model.tracers.history.copy()
    generations = model.tracers.generations.copy()

    model.recover_solver(FloatingPointError("unstable imported flow"))

    state = model.manager.solver.export_state()
    assert model.manager.solver.info.id == "pic-flip"
    assert model.manager.last_import is None
    assert state.time == pytest.approx(recovery_time)
    assert state.angle_degrees == 30.0
    assert model.time == pytest.approx(recovery_time)
    assert model.angle_override == 30.0
    assert model.previous_angle == 30.0
    assert not np.array_equal(model.tracers.positions, positions)
    assert not np.array_equal(model.tracers.history, history)
    np.testing.assert_array_equal(model.tracers.generations, generations + 1)
    assert np.isfinite(model.tracers.positions).all()
    x0, x1 = model.scenario.domain.bounds[0]
    y0, y1 = model.scenario.domain.bounds[1]
    assert np.all(model.tracers.positions[:, 0] >= x0)
    assert np.all(model.tracers.positions[:, 0] <= x1)
    assert np.all(model.tracers.positions[:, 1] >= y0)
    assert np.all(model.tracers.positions[:, 1] <= y1)
    assert not np.any(model.geometry.contains(model.tracers.positions, 30.0))
    assert np.all(model.tracers.ages >= 0.0)
    assert np.all(model.tracers.ages < model.tracers.lifetimes)
    for history_slice in model.tracers.history:
        np.testing.assert_array_equal(history_slice, model.tracers.positions)
    assert "recovered=fresh restart reason=nonfinite_state" in model.status()
    assert "recovery_epoch=1" in model.status()
    assert "step=   —/s" in model.status()
