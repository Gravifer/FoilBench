"""Typed viewer state, independent of the untyped OpenGL adapter."""

from dataclasses import dataclass
from time import perf_counter

import numpy as np

from foilbench_py.core.geometry import NacaFoil
from foilbench_py.core.metrics import vorticity
from foilbench_py.core.models import ControlState, Diagnostics, Scenario, StepReport
from foilbench_py.core.switching import SolverManager
from foilbench_py.core.tracers import TracerSystem
from foilbench_py.solvers.factory import create_solver, solver_ids
from foilbench_py.solvers.lbm import LBMSolver
from foilbench_py.solvers.pic_flip import PicFlipSolver
from foilbench_py.types import ScalarField


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
    last_diagnostics: Diagnostics | None = None
    diagnostic_elapsed: float = 0.0
    solver_steps_per_second: float = 0.0
    simulated_seconds_per_wall_second: float = 0.0
    vorticity_display: ScalarField | None = None
    vorticity_revision: int = 0
    show_vorticity: bool = True
    recovery_notice: str | None = None

    @classmethod
    def create(cls, scenario: Scenario, initial_solver: str = "stable-fluids") -> "ViewerModel":
        geometry = NacaFoil(scenario.foil)
        manager = SolverManager(create_solver, scenario, geometry, initial_solver)
        domain_area = np.prod([upper - lower for lower, upper in scenario.domain.bounds[:2]])
        tracer_count = int(np.clip(round(float(domain_area) * 256.0), 2_048, 8_192))
        tracers = TracerSystem.create(
            scenario.domain,
            geometry,
            count=tracer_count,
            history_length=12,
            seed=scenario.seed,
        )
        initial_angle = scenario.control_at(0.0).angle_degrees
        model = cls(
            scenario,
            geometry,
            manager,
            tracers,
            previous_angle=initial_angle,
        )
        model._refresh_diagnostics()
        return model

    def _refresh_diagnostics(self) -> None:
        solver = self.manager.solver
        self.last_diagnostics = solver.diagnostics()
        state = solver.export_state()
        omega = vorticity(state.velocity[0], self.scenario.domain)
        control = self.control(self.scenario.output_dt)
        solid = self.geometry.mask(self.scenario.domain, control.angle_degrees)
        omega = omega.copy()
        omega[solid] = 0.0
        fluid_magnitude = np.abs(omega[~solid])
        scale = (
            max(
                float(np.percentile(fluid_magnitude, 99.5)),
                0.2 * float(np.max(fluid_magnitude)),
            )
            if fluid_magnitude.size
            else 1.0
        )
        self.vorticity_display = np.asarray(
            np.tanh(omega / max(scale, 1.0e-6)),
            dtype=np.float32,
        )
        self.vorticity_revision += 1

    def control(self, dt: float) -> ControlState:
        scheduled = self.scenario.control_at(self.time)
        angle = scheduled.angle_degrees if self.angle_override is None else self.angle_override
        angular_velocity = (angle - self.previous_angle) / max(dt, 1.0e-9)
        return ControlState(self.time, angle, angular_velocity)

    def update(self, dt: float) -> None:
        if self.paused:
            return
        del dt
        simulation_dt = self.scenario.output_dt
        self.time += simulation_dt
        control = self.control(simulation_dt)
        started = perf_counter()
        self.last_report = self.manager.solver.advance(control, simulation_dt)
        solver_elapsed = max(perf_counter() - started, 1.0e-9)
        instantaneous_rate = 1.0 / solver_elapsed
        instantaneous_throughput = simulation_dt / solver_elapsed
        smoothing = 0.15
        if self.solver_steps_per_second == 0.0:
            self.solver_steps_per_second = instantaneous_rate
            self.simulated_seconds_per_wall_second = instantaneous_throughput
        else:
            self.solver_steps_per_second = (
                1.0 - smoothing
            ) * self.solver_steps_per_second + smoothing * instantaneous_rate
            self.simulated_seconds_per_wall_second = (
                1.0 - smoothing
            ) * self.simulated_seconds_per_wall_second + smoothing * instantaneous_throughput
        self.tracers.update(self.manager.solver, control, simulation_dt)
        self.previous_angle = control.angle_degrees
        self.diagnostic_elapsed += simulation_dt
        if self.diagnostic_elapsed >= 0.2:
            self._refresh_diagnostics()
            self.diagnostic_elapsed = 0.0

    def set_angle(self, angle_degrees: float) -> None:
        self.angle_override = float(np.clip(angle_degrees, -30.0, 30.0))

    def release_angle(self) -> None:
        if self.angle_override is not None:
            self.previous_angle = self.angle_override

    def switch_solver(self, solver_id: str) -> None:
        control = self.control(self.scenario.output_dt)
        self.manager.switch(solver_id, control)
        self.recovery_notice = None
        self._refresh_diagnostics()

    def recover_solver(self, failure: Exception) -> None:
        """Discard the active flow and restart its solver at the visible foil angle."""
        current_angle = self.control(self.scenario.output_dt).angle_degrees
        recovery_control = ControlState(0.0, current_angle, 0.0)
        self.manager.restart_at(recovery_control)
        self.time = 0.0
        self.angle_override = current_angle
        self.previous_angle = current_angle
        self.tracers.reseed_all(current_angle)
        self.last_report = None
        self.diagnostic_elapsed = 0.0
        self.solver_steps_per_second = 0.0
        self.simulated_seconds_per_wall_second = 0.0
        self.recovery_notice = f"fresh restart after {type(failure).__name__}"
        self._refresh_diagnostics()

    def reset(self) -> None:
        solver_id = self.manager.solver.info.id
        tracer_mode = self.tracers.mode
        show_vorticity = self.show_vorticity
        replacement = ViewerModel.create(self.scenario, solver_id)
        replacement.tracers.mode = tracer_mode
        replacement.show_vorticity = show_vorticity
        self.manager = replacement.manager
        self.tracers = replacement.tracers
        self.time = 0.0
        self.paused = False
        self.angle_override = None
        self.previous_angle = self.scenario.control_at(0.0).angle_degrees
        self.last_report = None
        self.last_diagnostics = replacement.last_diagnostics
        self.diagnostic_elapsed = 0.0
        self.solver_steps_per_second = 0.0
        self.simulated_seconds_per_wall_second = 0.0
        self.vorticity_display = replacement.vorticity_display
        self.vorticity_revision = replacement.vorticity_revision
        self.show_vorticity = replacement.show_vorticity
        self.recovery_notice = None

    def adjust_blend(self, delta: float) -> None:
        solver = self.manager.solver
        if isinstance(solver, PicFlipSolver):
            solver.blend = solver.blend + delta

    def toggle_vorticity(self) -> bool:
        self.show_vorticity = not self.show_vorticity
        return self.show_vorticity

    def toggle_tracer_mode(self) -> str:
        return self.tracers.toggle_mode()

    def status(self) -> str:
        solver = self.manager.solver
        diagnostics = self.last_diagnostics
        report = self.last_report
        substeps = 0 if report is None else report.substeps
        speed = 0.0 if report is None else report.max_speed
        blend = f"  blend={solver.blend:.2f}" if isinstance(solver, PicFlipSolver) else ""
        effective_reynolds = (
            f"  Re_eff={diagnostics.values.get('effective_reynolds', 0.0):.0f}"
            if isinstance(solver, LBMSolver) and diagnostics is not None
            else ""
        )
        warning = ""
        if self.manager.last_import is not None:
            warning = "  warm-import transient"
        if self.recovery_notice is not None:
            warning = f"  recovered={self.recovery_notice}"
        energy = 0.0 if diagnostics is None else diagnostics.values.get("kinetic_energy", 0.0)
        enstrophy = 0.0 if diagnostics is None else diagnostics.values.get("enstrophy", 0.0)
        control = self.control(self.scenario.output_dt)
        return (
            f"{solver.info.display_name}  t={self.time:6.2f}  "
            f"AoA={control.angle_degrees:5.1f}°  "
            f"step={self.solver_steps_per_second:4.1f}/s  "
            f"sim/wall={self.simulated_seconds_per_wall_second:4.2f}  "
            f"sub={substeps}  max|u|={speed:4.2f}  "
            f"E={energy:.3f}  Ω={enstrophy:.3f}  "
            f"tracers={self.tracers.mode}  vort={'on' if self.show_vorticity else 'off'}"
            f"{blend}{effective_reynolds}{warning}"
        )

    @property
    def available_solvers(self) -> tuple[str, ...]:
        return solver_ids()


def run_viewer(scenario: Scenario, initial_solver: str = "stable-fluids") -> None:
    from foilbench_py.viewer.gl_adapter import run_gl_window

    run_gl_window(ViewerModel.create(scenario, initial_solver))
