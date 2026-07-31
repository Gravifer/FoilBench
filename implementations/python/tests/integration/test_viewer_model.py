import numpy as np
import pytest

from foilbench_py.viewer.app import ViewerModel, viewer_bounds
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


def test_display_tracers_expire_without_leaving_empty_regions(
    scenario_factory: ScenarioFactory,
) -> None:
    scenario = scenario_factory(resolution=(32, 16))
    model = ViewerModel.create(scenario, "stable-fluids")
    before = model.tracers.positions.copy()
    model.tracers.ages[:] = model.tracers.lifetimes + 1.0

    model.update(0.01)

    assert model.tracers.mode == "display"
    assert np.all(model.tracers.ages == 0.0)
    assert not np.array_equal(before, model.tracers.positions)
    for history_slice in model.tracers.history:
        np.testing.assert_array_equal(history_slice, model.tracers.positions)
    assert model.toggle_tracer_mode() == "material"


def test_failed_warm_state_recovers_fresh_and_reseeds_tracers(
    scenario_factory: ScenarioFactory,
) -> None:
    model = ViewerModel.create(
        scenario_factory(resolution=(32, 16)),
        "stable-fluids",
    )
    model.update(model.scenario.output_dt)
    model.switch_solver("pic-flip")
    model.set_angle(30.0)
    positions = model.tracers.positions.copy()
    history = model.tracers.history.copy()

    model.recover_solver(FloatingPointError("unstable imported flow"))

    state = model.manager.solver.export_state()
    assert model.manager.solver.info.id == "pic-flip"
    assert model.manager.last_import is None
    assert state.time == 0.0
    assert state.angle_degrees == 30.0
    assert model.time == 0.0
    assert model.angle_override == 30.0
    assert model.previous_angle == 30.0
    assert not np.array_equal(model.tracers.positions, positions)
    assert not np.array_equal(model.tracers.history, history)
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
    assert "recovered=fresh restart after FloatingPointError" in model.status()
