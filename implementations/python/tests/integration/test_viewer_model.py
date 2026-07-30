import numpy as np

from foilbench_py.viewer.app import ViewerModel
from tests.helpers import ScenarioFactory


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
