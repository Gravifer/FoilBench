import json
from dataclasses import replace
from pathlib import Path
from typing import cast

import numpy as np
import pytest

from foilbench_py.core.geometry import NacaFoil
from foilbench_py.core.models import (
    ControlState,
    ImportOutcome,
    NumericalFailure,
    StepReport,
)
from foilbench_py.core.tracers import TracerSystem
from foilbench_py.solvers.pic_flip import PicFlipSolver
from foilbench_py.solvers.stable_fluids import StableFluidsSolver
from foilbench_py.types import PointCloud
from foilbench_py.viewer.app import (
    ViewerModel,
    normalize_vorticity_display,
    viewer_bounds,
    viewer_crop_enabled_by_default,
)
from tests.helpers import ScenarioFactory


class _RotationSampler(StableFluidsSolver):
    def sample_velocity(self, points: PointCloud) -> PointCloud:
        sampled = np.empty_like(points)
        sampled[:, 0] = -points[:, 1]
        sampled[:, 1] = points[:, 0]
        return sampled


def test_shared_vorticity_display_fixture() -> None:
    fixture_path = Path(__file__).parents[4] / "spec" / "conformance" / "vorticity-display.json"
    fixture = json.loads(fixture_path.read_text(encoding="utf-8"))
    recipe = fixture["synthetic"]
    raw = np.arange(recipe["linear_count"], dtype=np.float32) * np.float32(recipe["linear_step"])
    raw = np.concatenate((raw, np.asarray([recipe["outlier"]], dtype=np.float32)))

    solid = np.zeros_like(raw, dtype=np.bool_)
    solid[-1] = True
    masked = normalize_vorticity_display(raw.reshape(1, -1), solid.reshape(1, -1))
    assert masked[0, -1] == 0.0
    assert masked[0, -2] == pytest.approx(
        np.tanh(1.99 / recipe["solid_outlier_expected_scale"]), abs=1.0e-6
    )

    unmasked = normalize_vorticity_display(
        raw.reshape(1, -1), np.zeros((1, raw.size), dtype=np.bool_)
    )
    assert unmasked[0, -1] == pytest.approx(
        np.tanh(recipe["outlier"] / recipe["fluid_outlier_expected_scale"]), abs=1.0e-6
    )
    assert unmasked[0, -2] == pytest.approx(
        np.tanh(1.99 / recipe["fluid_outlier_expected_scale"]), abs=1.0e-6
    )


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
    assert not viewer_crop_enabled_by_default(scenario)

    scenario.solver_options["viewer_crop_default"] = True
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
    assert "div=" in model.status()
    assert "leak=" in model.status()
    model.paused = True
    assert "PAUSED" in model.status()
    model.paused = False
    generations = model.tracers.generations.copy()
    model.switch_solver("lbm-d2q9")
    assert model.manager.solver.info.id == "lbm-d2q9"
    generation_changes = model.tracers.generations - generations
    assert np.all((generation_changes == 0) | (generation_changes == 1))
    assert np.count_nonzero(generation_changes) < 0.1 * generation_changes.size
    assert model.time == pytest.approx(2.0 * scenario.output_dt)
    assert "warm-import transient" in model.status()


def test_status_derives_aerodynamic_aoa_without_changing_solver_pose(
    scenario_factory: ScenarioFactory,
) -> None:
    model = ViewerModel.create(scenario_factory(resolution=(24, 12)), "stable-fluids")
    model.set_angle(12.0, 1.0)
    assert model.control(0.0).angle_degrees == 12.0
    assert "AoA=-12.0°" in model.status()


def test_initial_tracers_respect_the_authoritative_foil_pose(
    scenario_factory: ScenarioFactory,
) -> None:
    scenario = scenario_factory(resolution=(32, 16))
    model = ViewerModel.create(scenario, "stable-fluids")
    initial_angle = scenario.control_at(0.0).angle_degrees

    assert not np.any(model.geometry.contains(model.tracers.positions, initial_angle))
    assert np.all(model.tracers.generations == 0)
    assert np.all(model.tracers.history_generations == 0)


def test_shared_tracer_fixture_uses_frozen_field_midpoint(
    scenario_factory: ScenarioFactory,
) -> None:
    repository_root = Path(__file__).resolve().parents[4]
    document = cast(
        dict[str, object],
        json.loads(
            (repository_root / "spec/conformance/tracer-lifecycle.json").read_text(
                encoding="utf-8"
            )
        ),
    )
    assert document["contract_id"] == "foilbench-phase2-v1"
    assert document["contract_revision"] == 4
    integrator = cast(dict[str, object], document["integrator"])
    initial = cast(list[float], integrator["initial_position"])
    expected = cast(list[float], integrator["expected_position"])
    timestep = cast(float, integrator["target_dt"])
    tolerance = cast(float, integrator["absolute_tolerance"])

    scenario = scenario_factory(resolution=(32, 16))
    tracers = TracerSystem.create(
        scenario.domain,
        NacaFoil(scenario.foil),
        count=1,
        history_length=3,
        seed=0,
        angle_degrees=0.0,
    )
    tracers.positions[0] = initial
    tracers.ages[0] = 0.0
    tracers.lifetimes[0] = 10.0
    tracers.update(_RotationSampler(), ControlState(timestep, 0.0, 0.0), timestep)
    np.testing.assert_allclose(tracers.positions[0], expected, atol=tolerance, rtol=0.0)


def test_first_step_after_reset_refreshes_warming_diagnostics(
    scenario_factory: ScenarioFactory,
) -> None:
    scenario = scenario_factory(resolution=(32, 16))
    model = ViewerModel.create(scenario, "stable-fluids")
    model.presentation.diagnostic_interval = 10.0
    model.update(scenario.output_dt)
    model.reset()

    assert model.metrics_warming
    assert model.tracers.recycle_counters["scenario_reset"] == model.tracers.positions.shape[0]
    assert model.last_diagnostics is not None
    assert model.last_diagnostics.values["time"] == 0.0

    model.update(scenario.output_dt)

    assert not model.metrics_warming
    assert model.last_diagnostics is not None
    assert model.last_diagnostics.values["time"] == pytest.approx(model.time)


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


def test_diagnostic_mode_toggles_and_survives_reset(
    scenario_factory: ScenarioFactory,
) -> None:
    model = ViewerModel.create(scenario_factory(resolution=(32, 16)), "stable-fluids")
    assert model.session_state.diagnostic_mode == "cadenced"

    assert model.toggle_diagnostics() == "every-step"
    model.reset()

    assert model.session_state.diagnostic_mode == "every-step"
    assert "diag=every-step" in model.status()


def test_transient_warm_rejection_falls_back_once_but_structural_rejection_does_not(
    scenario_factory: ScenarioFactory,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    model = ViewerModel.create(scenario_factory(resolution=(32, 16)), "stable-fluids")
    calls = 0

    def reject_transient(*args: object, **kwargs: object) -> ImportOutcome:
        del args, kwargs
        nonlocal calls
        calls += 1
        return ImportOutcome("rejected", "projection_failure")

    monkeypatch.setattr(model.manager, "switch", reject_transient)
    completed_time = model.time
    outcome = model.switch_solver("lbm-d2q9")
    assert outcome.accepted
    assert calls == 1
    assert model.manager.solver.info.id == "lbm-d2q9"
    assert model.recovery_stage == "warm-import-fallback"
    assert model.recovery_count == 1
    assert model.time == pytest.approx(completed_time + model.scenario.output_dt)
    assert model.last_report is not None

    model = ViewerModel.create(model.scenario, "stable-fluids")

    def reject_structural(*args: object, **kwargs: object) -> ImportOutcome:
        del args, kwargs
        return ImportOutcome("rejected", "incompatible_domain")

    monkeypatch.setattr(model.manager, "switch", reject_structural)
    outcome = model.switch_solver("lbm-d2q9")
    assert not outcome.accepted
    assert model.manager.solver.info.id == "stable-fluids"


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
    model.set_angle(13.0, 1.05)
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


def test_nonfinite_drag_sample_is_rejected_without_state_mutation(
    scenario_factory: ScenarioFactory,
) -> None:
    model = ViewerModel.create(scenario_factory(resolution=(32, 16)), "stable-fluids")
    before = (model.angle_override, model.drag_active, model.recovery_count)

    with pytest.raises(ValueError, match="pose angle"):
        model.set_angle(float("nan"), 1.0)
    with pytest.raises(ValueError, match="pose timestamp"):
        model.set_angle(5.0, float("inf"))

    assert (model.angle_override, model.drag_active, model.recovery_count) == before


def test_nonmonotonic_drag_sample_does_not_fabricate_a_tiny_interval(
    scenario_factory: ScenarioFactory,
) -> None:
    model = ViewerModel.create(scenario_factory(resolution=(32, 16)), "stable-fluids")
    model.set_angle(0.0, 1.0)
    model.set_angle(20.0, 1.01)
    assert model.control(model.scenario.output_dt).angular_velocity_degrees != 0.0

    model.set_angle(10.0, 1.005)

    assert model.control(model.scenario.output_dt).angular_velocity_degrees == 0.0


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
    model.manager.solver.advance(
        ControlState(
            control.time + scenario.output_dt,
            control.angle_degrees,
            control.angular_velocity_degrees,
        ),
        scenario.output_dt,
    )

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
    tracers = TracerSystem.create(
        scenario.domain,
        NacaFoil(scenario.foil),
        count=1,
        history_length=3,
        seed=0,
        angle_degrees=0.0,
    )
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
    assert tracers.recycle_counters["periodic_wrap"] == 1


def test_tracer_overlap_commits_only_lifetime_expiry(
    scenario_factory: ScenarioFactory,
) -> None:
    scenario = scenario_factory(
        resolution=(32, 16), periodic_axes=("x", "y"), foil_in_domain=False
    )
    model = ViewerModel.create(scenario, "stable-fluids")
    tracers = TracerSystem.create(
        scenario.domain,
        NacaFoil(scenario.foil),
        count=1,
        history_length=3,
        seed=0,
        angle_degrees=0.0,
    )
    tracer = 0
    x1 = scenario.domain.bounds[0][1]
    tracers.positions[tracer] = (x1 - 0.0025, 0.0)
    tracers.ages[tracer] = 1.0
    tracers.lifetimes[tracer] = 1.0
    generation = int(tracers.generations[tracer])
    counters_before = dict(tracers.recycle_counters)

    tracers.update(
        model.manager.solver,
        scenario.control_at(scenario.output_dt),
        scenario.output_dt,
    )

    assert tracers.generations[tracer] == generation + 1
    assert (
        tracers.recycle_counters["lifetime_expiry"]
        - counters_before["lifetime_expiry"]
        >= 1
    )
    assert (
        tracers.recycle_counters["periodic_wrap"]
        - counters_before["periodic_wrap"]
        == 0
    )


def test_failed_warm_state_recovers_fresh_and_reseeds_tracers(
    scenario_factory: ScenarioFactory,
) -> None:
    model = ViewerModel.create(
        scenario_factory(resolution=(32, 16)),
        "stable-fluids",
    )
    model.update(model.scenario.output_dt)
    model.switch_solver("pic-flip")
    recovery_time = model.time
    model.set_angle(30.0)
    positions = model.tracers.positions.copy()
    history = model.tracers.history.copy()
    generations = model.tracers.generations.copy()
    model.last_requested_angular_velocity_degrees = 600.0

    model.recover_solver(FloatingPointError("unstable imported flow"))

    state = model.manager.solver.export_state()
    assert model.manager.solver.info.id == "pic-flip"
    assert model.manager.last_import is not None
    assert state.time == pytest.approx(recovery_time + model.scenario.output_dt)
    assert state.angle_degrees == 30.0
    assert model.time == pytest.approx(recovery_time + model.scenario.output_dt)
    assert model.angle_override == 30.0
    assert model.previous_angle == 30.0
    assert model.last_requested_angular_velocity_degrees == 0.0
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
    assert "step=   —/s" not in model.status()


def test_failed_solver_advance_does_not_commit_physical_time(
    scenario_factory: ScenarioFactory,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    model = ViewerModel.create(scenario_factory(resolution=(32, 16)), "stable-fluids")
    solver = model.manager.solver
    starting_time = model.time

    def fail_advance(control: object, target_dt: float) -> None:
        del control, target_dt
        raise FloatingPointError("injected solver failure")

    monkeypatch.setattr(solver, "advance", fail_advance)

    with pytest.raises(FloatingPointError, match="injected solver failure"):
        model.update(model.scenario.output_dt)

    assert model.time == starting_time


def test_first_ordinary_failure_after_switch_is_post_import(
    scenario_factory: ScenarioFactory,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    scenario = scenario_factory(resolution=(32, 16))
    model = ViewerModel.create(scenario, "lbm-d2q9")

    assert model.switch_solver("stable-fluids").accepted
    assert model.warm_validation_pending
    solver = model.manager.solver
    assert isinstance(solver, StableFluidsSolver)

    def fail_post_import_step(
        _solver: StableFluidsSolver,
        _control: ControlState,
        _target_dt: float,
    ) -> StepReport:
        raise NumericalFailure("nonfinite_state", "injected post-import failure")

    monkeypatch.setattr(StableFluidsSolver, "advance", fail_post_import_step)

    with pytest.raises(NumericalFailure) as raised:
        model.update(scenario.output_dt)

    assert model.warm_validation_pending
    monkeypatch.undo()
    model.recover_solver(
        raised.value,
        post_import=model.warm_validation_pending,
    )
    assert model.recovery_stage == "post-import"
    assert "stage=post-import" in model.status()

    assert model.switch_solver("pic-flip").accepted
    assert model.warm_validation_pending
    model.update(scenario.output_dt)
    assert not model.warm_validation_pending


def test_scenario_reset_does_not_reuse_recovery_epoch(
    scenario_factory: ScenarioFactory,
) -> None:
    model = ViewerModel.create(scenario_factory(resolution=(32, 16)), "stable-fluids")
    model.recover_solver(FloatingPointError("first injected failure"))
    assert model.recovery_count == 1

    model.reset()
    assert model.recovery_count == 1
    model.recover_solver(FloatingPointError("second injected failure"))

    assert model.recovery_count == 2
    assert "recovery_epoch=2" in model.status()
