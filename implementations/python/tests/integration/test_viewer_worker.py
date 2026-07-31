import numpy as np

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

        switched = worker.wait_for_command(worker.switch_solver("pic-flip"))
        assert switched.simulation_time == paused_time
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
