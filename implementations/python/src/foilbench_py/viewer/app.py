"""Typed viewer state, independent of the untyped OpenGL adapter."""

import math
from dataclasses import dataclass
from time import perf_counter

import numpy as np

from foilbench_py.core.geometry import NacaFoil
from foilbench_py.core.metrics import vorticity
from foilbench_py.core.models import ControlState, Diagnostics, Scenario, StepReport
from foilbench_py.core.state_io import midspan_velocity
from foilbench_py.core.switching import SolverManager
from foilbench_py.core.tracers import TracerSystem
from foilbench_py.solvers.factory import create_solver, solver_ids
from foilbench_py.solvers.lbm import LBMSolver
from foilbench_py.solvers.pic_flip import PicFlipSolver
from foilbench_py.types import ScalarField


def viewer_bounds(
    scenario: Scenario,
    cropped: bool = True,
) -> tuple[tuple[float, float], tuple[float, float]]:
    """Return optional presentation-only bounds inset from the solver domain."""
    full_bounds = (scenario.domain.bounds[0], scenario.domain.bounds[1])
    if not cropped:
        return full_bounds
    crop_option = scenario.solver_options.get("viewer_crop_cells", 0)
    if not isinstance(crop_option, int) or isinstance(crop_option, bool):
        raise TypeError("viewer_crop_cells must be an integer")
    if crop_option < 0:
        raise ValueError("viewer_crop_cells cannot be negative")
    if 2 * crop_option >= min(scenario.domain.nx, scenario.domain.ny) - 2:
        raise ValueError("viewer crop must leave at least three cells per axis")
    x0, x1 = full_bounds[0]
    y0, y1 = full_bounds[1]
    return (
        (x0 + crop_option * scenario.domain.dx, x1 - crop_option * scenario.domain.dx),
        (y0 + crop_option * scenario.domain.dy, y1 - crop_option * scenario.domain.dy),
    )


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
    drag_active: bool = False
    pose_only_drag: bool = False
    pose_only_release_pending: bool = False
    last_requested_angular_velocity_degrees: float = 0.0

    @property
    def playback_rate(self) -> float:
        exponent = math.log(1.5) / math.log(10.0)
        relative_reynolds = self.manager.reynolds / self.scenario.reynolds
        return float(np.clip(relative_reynolds**exponent, 0.5, 2.0))

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
        omega = vorticity(midspan_velocity(state), self.scenario.domain)
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
        if self.pose_only_drag:
            angular_velocity = 0.0
        return ControlState(self.time, angle, angular_velocity)

    @property
    def rapid_drag_attempted(self) -> bool:
        """Whether the last requested drag moved a foil tip faster than freestream."""
        tip_speed = (
            abs(np.deg2rad(self.last_requested_angular_velocity_degrees))
            * self.scenario.foil.chord
        )
        return self.drag_active and bool(tip_speed > self.scenario.reference_speed)

    def update(self, dt: float) -> None:
        if self.paused:
            return
        del dt
        simulation_dt = self.scenario.output_dt * self.playback_rate
        self.time += simulation_dt
        scheduled = self.scenario.control_at(self.time)
        angle = scheduled.angle_degrees if self.angle_override is None else self.angle_override
        self.last_requested_angular_velocity_degrees = (angle - self.previous_angle) / max(
            simulation_dt,
            1.0e-9,
        )
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
        if self.pose_only_release_pending and not self.drag_active:
            self.pose_only_drag = False
            self.pose_only_release_pending = False

    def set_angle(self, angle_degrees: float) -> None:
        self.angle_override = float(np.clip(angle_degrees, -30.0, 30.0))
        self.drag_active = True

    def release_angle(self) -> None:
        if self.angle_override is not None:
            self.previous_angle = self.angle_override
        self.drag_active = False
        self.pose_only_release_pending = self.pose_only_drag

    def enable_pose_only_drag(self) -> None:
        """Keep following the pointer while suppressing unresolved wall rotation."""
        self.pose_only_drag = True
        self.pose_only_release_pending = False

    def switch_solver(self, solver_id: str) -> None:
        control = self.control(self.scenario.output_dt)
        self.manager.switch(solver_id, control)
        self.recovery_notice = None
        self.pose_only_drag = False
        self.pose_only_release_pending = False
        self._refresh_diagnostics()

    def recover_solver(self, failure: Exception, reset_reynolds: bool = False) -> None:
        """Discard the active flow and restart its solver at the visible foil angle."""
        current_angle = self.control(self.scenario.output_dt).angle_degrees
        previous_reynolds = self.manager.reynolds
        if reset_reynolds:
            self.reset_reynolds()
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
        reynolds_notice = (
            f"; Re reset {previous_reynolds:.0f}->{self.manager.reynolds:.0f}"
            if reset_reynolds and previous_reynolds != self.manager.reynolds
            else ""
        )
        self.recovery_notice = (
            f"fresh restart after {type(failure).__name__}{reynolds_notice}"
        )
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
        self.drag_active = False
        self.pose_only_drag = False
        self.pose_only_release_pending = False
        self.last_requested_angular_velocity_degrees = 0.0

    def adjust_blend(self, delta: float) -> None:
        solver = self.manager.solver
        if isinstance(solver, PicFlipSolver):
            solver.blend = solver.blend + delta

    def set_reynolds(self, reynolds: float) -> None:
        selected = float(np.clip(reynolds, 50.0, 100_000.0))
        self.manager.set_reynolds(selected)

    def adjust_reynolds(self, decades: float) -> None:
        self.set_reynolds(self.manager.reynolds * 10.0**decades)

    def reset_reynolds(self) -> None:
        self.set_reynolds(self.scenario.reynolds)

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
        motion_mode = "  motion=pose-only" if self.pose_only_drag else ""
        energy = 0.0 if diagnostics is None else diagnostics.values.get("kinetic_energy", 0.0)
        enstrophy = 0.0 if diagnostics is None else diagnostics.values.get("enstrophy", 0.0)
        control = self.control(self.scenario.output_dt)
        return (
            f"{solver.info.display_name}  t={self.time:6.2f}  "
            f"AoA={control.angle_degrees:5.1f}°  "
            f"Re={self.manager.reynolds:7.0f}  rate={self.playback_rate:4.2f}x  "
            f"step={self.solver_steps_per_second:4.1f}/s  "
            f"sim/wall={self.simulated_seconds_per_wall_second:4.2f}  "
            f"sub={substeps}  max|u|={speed:4.2f}  "
            f"E={energy:.3f}  Ω={enstrophy:.3f}  "
            f"tracers={self.tracers.mode}  vort={'on' if self.show_vorticity else 'off'}"
            f"{blend}{effective_reynolds}{motion_mode}{warning}"
        )

    @property
    def available_solvers(self) -> tuple[str, ...]:
        return solver_ids()


def run_viewer(scenario: Scenario, initial_solver: str = "stable-fluids") -> None:
    from foilbench_py.viewer.gl_adapter import run_gl_window

    run_gl_window(ViewerModel.create(scenario, initial_solver))
