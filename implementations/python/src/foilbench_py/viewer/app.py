"""Typed viewer state, independent of the untyped OpenGL adapter."""

from dataclasses import dataclass

import numpy as np

from foilbench_py.core.geometry import NacaFoil
from foilbench_py.core.models import ControlState, Scenario, StepReport
from foilbench_py.core.switching import SolverManager
from foilbench_py.core.tracers import TracerSystem
from foilbench_py.solvers.factory import create_solver, solver_ids
from foilbench_py.solvers.pic_flip import PicFlipSolver


@dataclass(slots=True)
class ViewerModel:
    scenario: Scenario
    geometry: NacaFoil
    manager: SolverManager
    tracers: TracerSystem
    time: float = 0.0
    paused: bool = False
    angle_override: float | None = None
    previous_angle: float = 0.0
    last_report: StepReport | None = None

    @classmethod
    def create(cls, scenario: Scenario, initial_solver: str = "stable-fluids") -> "ViewerModel":
        geometry = NacaFoil(scenario.foil)
        manager = SolverManager(create_solver, scenario, geometry, initial_solver)
        tracers = TracerSystem.create(
            scenario.domain, geometry, count=8192, history_length=12, seed=scenario.seed
        )
        initial_angle = scenario.control_at(0.0).angle_degrees
        return cls(
            scenario,
            geometry,
            manager,
            tracers,
            previous_angle=initial_angle,
        )

    def control(self, dt: float) -> ControlState:
        scheduled = self.scenario.control_at(self.time)
        angle = scheduled.angle_degrees if self.angle_override is None else self.angle_override
        angular_velocity = (angle - self.previous_angle) / max(dt, 1.0e-9)
        return ControlState(self.time, angle, angular_velocity)

    def update(self, dt: float) -> None:
        if self.paused:
            return
        dt = min(max(dt, 1.0e-5), 1.0 / 30.0)
        self.time += dt
        control = self.control(dt)
        self.last_report = self.manager.solver.advance(control, dt)
        self.tracers.update(self.manager.solver, control, dt)
        self.previous_angle = control.angle_degrees

    def set_angle(self, angle_degrees: float) -> None:
        self.angle_override = float(np.clip(angle_degrees, -30.0, 30.0))

    def release_angle(self) -> None:
        if self.angle_override is not None:
            self.previous_angle = self.angle_override

    def switch_solver(self, solver_id: str) -> None:
        control = self.control(self.scenario.output_dt)
        self.manager.switch(solver_id, control)

    def reset(self) -> None:
        solver_id = self.manager.solver.info.id
        replacement = ViewerModel.create(self.scenario, solver_id)
        self.manager = replacement.manager
        self.tracers = replacement.tracers
        self.time = 0.0
        self.paused = False
        self.angle_override = None
        self.previous_angle = self.scenario.control_at(0.0).angle_degrees
        self.last_report = None

    def adjust_blend(self, delta: float) -> None:
        solver = self.manager.solver
        if isinstance(solver, PicFlipSolver):
            solver.blend = solver.blend + delta

    def status(self) -> str:
        solver = self.manager.solver
        diagnostics = solver.diagnostics()
        report = self.last_report
        substeps = 0 if report is None else report.substeps
        speed = 0.0 if report is None else report.max_speed
        blend = f"  blend={solver.blend:.2f}" if isinstance(solver, PicFlipSolver) else ""
        warning = ""
        if self.manager.last_import is not None:
            warning = "  warm-import transient"
        return (
            f"{solver.info.display_name}  t={self.time:6.2f}  "
            f"substeps={substeps}  max|u|={speed:5.2f}  "
            f"E={diagnostics.values.get('kinetic_energy', 0.0):.3f}"
            f"{blend}{warning}"
        )

    @property
    def available_solvers(self) -> tuple[str, ...]:
        return solver_ids()


def run_viewer(scenario: Scenario, initial_solver: str = "stable-fluids") -> None:
    from foilbench_py.viewer.gl_adapter import run_gl_window

    run_gl_window(ViewerModel.create(scenario, initial_solver))
