"""Typed viewer state, independent of the untyped OpenGL adapter."""

import math
from collections import deque
from dataclasses import dataclass, field
from time import perf_counter
from typing import Literal

import numpy as np

from foilbench_py.core.geometry import NacaFoil
from foilbench_py.core.metrics import vorticity
from foilbench_py.core.models import (
    ControlState,
    Diagnostics,
    ImportFailureReason,
    ImportOutcome,
    InteractiveTuning,
    NumericalFailure,
    Scenario,
    StepReport,
)
from foilbench_py.core.state_io import midspan_velocity
from foilbench_py.core.switching import SolverManager, classify_import_failure
from foilbench_py.core.tracers import TracerSystem
from foilbench_py.solvers.factory import create_solver, solver_ids
from foilbench_py.types import ScalarField

_POSE_ONLY_RELEASE_SPEED_RATIO = 0.5
_POSE_ONLY_RELEASE_STEPS = 2
_POSE_SAMPLE_WINDOW_SECONDS = 0.08
_MAX_RESOLVED_TIP_SPEED_RATIO = 8.0
_TRANSIENT_IMPORT_FAILURES: frozenset[ImportFailureReason] = frozenset(
    (
        "excessive_velocity",
        "stability_limit",
        "nonfinite_state",
        "convergence_failure",
        "projection_failure",
        "invalid_density",
        "invalid_population",
        "transfer_failure",
        "postcondition_failure",
    )
)

type DiagnosticMode = Literal["cadenced", "every-step"]
type ViewerPhase = Literal["warming", "running", "recovering", "paused", "failed"]
type MotionMode = Literal["resolved", "pose-only"]


@dataclass(frozen=True, slots=True)
class PoseSample:
    timestamp: float
    angle_degrees: float


@dataclass(slots=True)
class PresentationState:
    show_vorticity: bool
    crop_enabled: bool
    diagnostic_mode: DiagnosticMode = "cadenced"
    diagnostic_interval: float = 0.1
    diagnostic_elapsed: float = 0.0


@dataclass(frozen=True, slots=True)
class RecoveryRecord:
    epoch: int
    reason: str
    stage: str
    discarded_private_state: bool = True


@dataclass(frozen=True, slots=True)
class ViewerSessionState:
    phase: ViewerPhase
    motion_mode: MotionMode
    schedule_active: bool
    diagnostic_mode: DiagnosticMode
    recovery: RecoveryRecord | None


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


def viewer_crop_enabled_by_default(scenario: Scenario) -> bool:
    """Return whether a configured presentation crop should start enabled."""
    full_bounds = viewer_bounds(scenario, cropped=False)
    cropped_bounds = viewer_bounds(scenario, cropped=True)
    crop_available = cropped_bounds != full_bounds
    configured = scenario.solver_options.get("viewer_crop_default", False)
    if not isinstance(configured, bool):
        raise TypeError("viewer_crop_default must be a boolean")
    return crop_available and configured


@dataclass(slots=True)
class ViewerModel:
    scenario: Scenario
    geometry: NacaFoil
    manager: SolverManager
    tracers: TracerSystem
    presentation: PresentationState
    time: float = 0.0
    paused: bool = False
    angle_override: float | None = None
    previous_angle: float = 0.0
    last_report: StepReport | None = None
    last_diagnostics: Diagnostics | None = None
    solver_steps_per_second: float = 0.0
    simulated_seconds_per_wall_second: float = 0.0
    vorticity_display: ScalarField | None = None
    vorticity_revision: int = 0
    vorticity_solver_state_revision: int | None = None
    recovery_notice: str | None = None
    drag_active: bool = False
    pose_only_drag: bool = False
    pose_only_release_pending: bool = False
    pose_only_calm_steps: int = 0
    pose_only_guarded_trial: bool = False
    last_requested_angular_velocity_degrees: float = 0.0
    tuning_values: dict[str, str | float] = field(default_factory=lambda: {})
    tuning_notice: str | None = None
    manual_angular_velocity_degrees: float = 0.0
    pose_samples: deque[PoseSample] = field(default_factory=lambda: deque[PoseSample]())
    last_pose_received_at: float | None = None
    recovery_count: int = 0
    recovery_reason: str | None = None
    recovery_stage: str | None = None
    metrics_warming: bool = True
    warm_validation_pending: bool = False
    presentation_failure: str | None = None
    solver_epoch: int = 0

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
            PresentationState(
                show_vorticity=True,
                crop_enabled=viewer_crop_enabled_by_default(scenario),
            ),
            previous_angle=initial_angle,
        )
        model._remember_active_tuning()
        model._refresh_diagnostics()
        return model

    @property
    def show_vorticity(self) -> bool:
        return self.presentation.show_vorticity

    @show_vorticity.setter
    def show_vorticity(self, selected: bool) -> None:
        self.presentation.show_vorticity = selected

    @property
    def crop_enabled(self) -> bool:
        return self.presentation.crop_enabled

    @property
    def session_state(self) -> ViewerSessionState:
        if self.paused:
            phase: ViewerPhase = "failed" if self.presentation_failure else "paused"
        elif self.metrics_warming:
            phase = "warming"
        else:
            phase = "running"
        recovery = (
            RecoveryRecord(
                self.recovery_count,
                self.recovery_reason,
                self.recovery_stage,
            )
            if self.recovery_count and self.recovery_reason and self.recovery_stage
            else None
        )
        return ViewerSessionState(
            phase,
            "pose-only" if self.pose_only_drag else "resolved",
            self.angle_override is None,
            self.presentation.diagnostic_mode,
            recovery,
        )

    def _refresh_diagnostics(self, *, force_vorticity: bool = False) -> None:
        solver = self.manager.solver
        diagnostics = solver.diagnostics()
        if diagnostics.state_revision != solver.state_revision:
            raise RuntimeError("solver diagnostics describe a stale state revision")
        self.last_diagnostics = diagnostics
        if not (self.show_vorticity or force_vorticity):
            return
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
        self.vorticity_solver_state_revision = solver.state_revision

    def control(self, dt: float) -> ControlState:
        del dt
        scheduled = self.scenario.control_at(self.time)
        if self.angle_override is None:
            angle = scheduled.angle_degrees
            angular_velocity = scheduled.angular_velocity_degrees
        else:
            angle = self.angle_override
            angular_velocity = self.manual_angular_velocity_degrees
        if self.pose_only_drag:
            angular_velocity = 0.0
        return ControlState(self.time, angle, angular_velocity)

    @property
    def requested_tip_speed_ratio(self) -> float:
        tip_speed = (
            abs(np.deg2rad(self.last_requested_angular_velocity_degrees))
            * self.scenario.foil.chord
        )
        return float(tip_speed / self.scenario.reference_speed)

    @property
    def rapid_drag_attempted(self) -> bool:
        """Whether the last requested drag moved a foil tip faster than freestream."""
        return self.drag_active and self.requested_tip_speed_ratio > 1.0

    def _disable_pose_only_drag(self, *, guard_next_failure: bool = False) -> None:
        self.pose_only_drag = False
        self.pose_only_release_pending = False
        self.pose_only_calm_steps = 0
        self.pose_only_guarded_trial = guard_next_failure

    def _pause_for_presentation_failure(self, stage: str, error: Exception) -> None:
        self.presentation_failure = f"{stage} {type(error).__name__}: {error}"
        self.paused = True

    def _settle_idle_drag(self) -> None:
        if (
            not self.drag_active
            or self.last_pose_received_at is None
            or perf_counter() - self.last_pose_received_at <= _POSE_SAMPLE_WINDOW_SECONDS
        ):
            return
        self.manual_angular_velocity_degrees = 0.0
        self.last_requested_angular_velocity_degrees = 0.0
        self.pose_samples.clear()
        self.last_pose_received_at = None

    def update(self, dt: float) -> None:
        if self.paused:
            return
        self._settle_idle_drag()
        was_warming = self.metrics_warming
        del dt
        simulation_dt = self.scenario.output_dt * self.playback_rate
        next_time = self.time + simulation_dt
        scheduled = self.scenario.control_at(next_time)
        self.last_requested_angular_velocity_degrees = (
            scheduled.angular_velocity_degrees
            if self.angle_override is None
            else self.manual_angular_velocity_degrees
        )
        angular_velocity = self.last_requested_angular_velocity_degrees
        if self.pose_only_drag:
            angular_velocity = 0.0
        control = ControlState(
            next_time,
            scheduled.angle_degrees if self.angle_override is None else self.angle_override,
            angular_velocity,
        )
        started = perf_counter()
        self.last_report = self.manager.solver.advance(control, simulation_dt)
        self.time = next_time
        self.warm_validation_pending = False
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
        self.previous_angle = control.angle_degrees
        try:
            self.tracers.update(self.manager.solver, control, simulation_dt)
        except Exception as error:
            self.last_diagnostics = None
            self.metrics_warming = True
            self._pause_for_presentation_failure("tracer", error)
            return
        self.presentation.diagnostic_elapsed += simulation_dt
        if self.presentation.diagnostic_mode == "every-step" or was_warming or (
            self.presentation.diagnostic_elapsed >= self.presentation.diagnostic_interval
        ):
            try:
                self._refresh_diagnostics()
            except Exception as error:
                self.last_diagnostics = None
                self.metrics_warming = True
                self._pause_for_presentation_failure("diagnostic", error)
                return
            self.presentation.diagnostic_elapsed = 0.0
        self.metrics_warming = False
        self.presentation_failure = None
        if self.pose_only_drag:
            if self.pose_only_release_pending and not self.drag_active:
                self._disable_pose_only_drag(guard_next_failure=True)
            elif self.requested_tip_speed_ratio <= _POSE_ONLY_RELEASE_SPEED_RATIO:
                self.pose_only_calm_steps += 1
                if self.pose_only_calm_steps >= _POSE_ONLY_RELEASE_STEPS:
                    self._disable_pose_only_drag(guard_next_failure=True)
            else:
                self.pose_only_calm_steps = 0

    def set_angle(self, angle_degrees: float, timestamp: float | None = None) -> None:
        selected = float(np.clip(angle_degrees, -30.0, 30.0))
        selected_time = perf_counter() if timestamp is None else timestamp
        self.last_pose_received_at = perf_counter()
        if self.pose_samples and (
            selected_time <= self.pose_samples[-1].timestamp
            or selected_time - self.pose_samples[-1].timestamp
            > _POSE_SAMPLE_WINDOW_SECONDS
        ):
            self.pose_samples.clear()
            self.manual_angular_velocity_degrees = 0.0
        self.pose_samples.append(PoseSample(selected_time, selected))
        cutoff = selected_time - _POSE_SAMPLE_WINDOW_SECONDS
        while len(self.pose_samples) > 2 and self.pose_samples[1].timestamp < cutoff:
            self.pose_samples.popleft()
        measured = 0.0
        if len(self.pose_samples) >= 2:
            first = self.pose_samples[0]
            elapsed = selected_time - first.timestamp
            measured = (selected - first.angle_degrees) / elapsed
        maximum = math.degrees(
            _MAX_RESOLVED_TIP_SPEED_RATIO
            * self.scenario.reference_speed
            / self.scenario.foil.chord
        )
        self.manual_angular_velocity_degrees = float(np.clip(measured, -maximum, maximum))
        self.last_requested_angular_velocity_degrees = self.manual_angular_velocity_degrees
        self.angle_override = selected
        self.drag_active = True

    def release_angle(self) -> None:
        if self.angle_override is not None:
            self.previous_angle = self.angle_override
        self.manual_angular_velocity_degrees = 0.0
        self.pose_samples.clear()
        self.last_pose_received_at = None
        self.drag_active = False
        self.pose_only_release_pending = self.pose_only_drag

    def enable_pose_only_drag(self) -> None:
        """Keep following the pointer while suppressing unresolved wall rotation."""
        self.pose_only_drag = True
        self.pose_only_release_pending = False
        self.pose_only_calm_steps = 0
        self.pose_only_guarded_trial = False

    def switch_solver(self, solver_id: str) -> ImportOutcome:
        self._remember_active_tuning()
        current_control = self.control(self.scenario.output_dt)
        if solver_id == self.manager.solver.info.id:
            return self.manager.switch(
                solver_id,
                current_control,
                current_control,
                self.scenario.output_dt,
            )
        validation_dt = self.scenario.output_dt * self.playback_rate
        validation_time = self.time + validation_dt
        scheduled = self.scenario.control_at(validation_time)
        validation_angular_velocity = (
            scheduled.angular_velocity_degrees
            if self.angle_override is None
            else self.manual_angular_velocity_degrees
        )
        if self.pose_only_drag:
            validation_angular_velocity = 0.0
        validation_control = ControlState(
            validation_time,
            scheduled.angle_degrees if self.angle_override is None else self.angle_override,
            validation_angular_velocity,
        )
        outcome = self.manager.switch(
            solver_id,
            current_control,
            validation_control,
            validation_dt,
        )
        if not outcome.accepted:
            if outcome.reason in _TRANSIENT_IMPORT_FAILURES:
                fresh_control = ControlState(self.time, current_control.angle_degrees, 0.0)
                fresh = self.manager.switch_fresh(solver_id, fresh_control)
                if fresh.accepted:
                    self._apply_saved_tuning()
                    self.solver_epoch += 1
                    self.angle_override = current_control.angle_degrees
                    self.previous_angle = current_control.angle_degrees
                    self.manual_angular_velocity_degrees = 0.0
                    self.pose_samples.clear()
                    self.last_pose_received_at = None
                    self.tracers.reseed_all(current_control.angle_degrees)
                    self.recovery_count += 1
                    self.recovery_reason = outcome.reason
                    self.recovery_stage = "warm-import-fallback"
                    self.recovery_notice = (
                        f"fresh destination reason={outcome.reason}; "
                        "stage=warm-import-fallback; private-state-discarded"
                    )
                    self.last_report = None
                    self.last_diagnostics = None
                    self.presentation.diagnostic_elapsed = 0.0
                    self.solver_steps_per_second = 0.0
                    self.simulated_seconds_per_wall_second = 0.0
                    self.metrics_warming = True
                    self.warm_validation_pending = False
                    self.presentation_failure = None
                    try:
                        self._refresh_diagnostics()
                    except Exception as error:
                        self._pause_for_presentation_failure("diagnostic", error)
                    self.last_diagnostics = None
                    return fresh
                self.recovery_notice = (
                    f"warm import rejected ({outcome.reason}); fresh destination "
                    f"rejected ({fresh.reason}); source retained"
                )
                return outcome
            self.tuning_notice = None
            self.recovery_notice = f"warm import rejected ({outcome.reason}); source retained"
            return outcome
        self._apply_saved_tuning()
        self.solver_epoch += 1
        self.recovery_notice = None
        self.tuning_notice = None
        self.time = validation_time
        self.last_report = self.manager.last_validation_report
        elapsed = self.manager.last_validation_elapsed
        self.solver_steps_per_second = 1.0 / elapsed
        self.simulated_seconds_per_wall_second = validation_dt / elapsed
        self.previous_angle = validation_control.angle_degrees
        try:
            self.tracers.update(self.manager.solver, validation_control, validation_dt)
            self._refresh_diagnostics()
        except Exception as error:
            self.last_diagnostics = None
            self.metrics_warming = True
            self._pause_for_presentation_failure("warm-switch presentation", error)
        else:
            self.metrics_warming = False
            self.presentation_failure = None
        self.warm_validation_pending = True
        return outcome

    def recover_solver(
        self,
        failure: ValueError | FloatingPointError | NumericalFailure,
        reset_reynolds: bool = False,
        post_import: bool = False,
    ) -> None:
        """Discard the active flow and restart its solver at the visible foil angle."""
        current_angle = self.control(self.scenario.output_dt).angle_degrees
        current_time = self.time
        previous_reynolds = self.manager.reynolds
        if reset_reynolds:
            self.reset_reynolds()
        recovery_control = ControlState(current_time, current_angle, 0.0)
        self.manager.restart_at(recovery_control)
        self._apply_saved_tuning()
        self.solver_epoch += 1
        self.angle_override = current_angle
        self.previous_angle = current_angle
        self.manual_angular_velocity_degrees = 0.0
        self.pose_samples.clear()
        self.last_pose_received_at = None
        self.tracers.reseed_all(current_angle)
        self.last_report = None
        self.last_diagnostics = None
        self.presentation.diagnostic_elapsed = 0.0
        self.solver_steps_per_second = 0.0
        self.simulated_seconds_per_wall_second = 0.0
        self.metrics_warming = True
        self.warm_validation_pending = False
        self.recovery_count += 1
        self.recovery_reason = classify_import_failure(failure)
        self.recovery_stage = "post-import" if post_import else "ordinary-step"
        reynolds_notice = (
            f"; Re reset {previous_reynolds:.0f}->{self.manager.reynolds:.0f}"
            if reset_reynolds and previous_reynolds != self.manager.reynolds
            else ""
        )
        self.recovery_notice = (
            f"fresh restart reason={self.recovery_reason}; "
            f"stage={self.recovery_stage}; private-state-discarded{reynolds_notice}"
        )
        self.presentation_failure = None
        try:
            self._refresh_diagnostics()
        except Exception as error:
            self._pause_for_presentation_failure("diagnostic", error)
        self.last_diagnostics = None

    def reset(self) -> None:
        solver_id = self.manager.solver.info.id
        tracer_mode = self.tracers.mode
        presentation = PresentationState(
            self.show_vorticity,
            self.crop_enabled,
            self.presentation.diagnostic_mode,
            self.presentation.diagnostic_interval,
        )
        replacement = ViewerModel.create(self.scenario, solver_id)
        replacement.tracers.mode = tracer_mode
        replacement.presentation = presentation
        self.manager = replacement.manager
        self.solver_epoch += 1
        self.tracers = replacement.tracers
        self.time = 0.0
        self.paused = False
        self.angle_override = None
        self.previous_angle = self.scenario.control_at(0.0).angle_degrees
        self.last_report = None
        self.last_diagnostics = replacement.last_diagnostics
        self.presentation.diagnostic_elapsed = 0.0
        self.solver_steps_per_second = 0.0
        self.simulated_seconds_per_wall_second = 0.0
        self.vorticity_display = replacement.vorticity_display
        self.vorticity_revision = replacement.vorticity_revision
        self.vorticity_solver_state_revision = replacement.vorticity_solver_state_revision
        self.presentation = replacement.presentation
        self.recovery_notice = None
        self.drag_active = False
        self._disable_pose_only_drag()
        self.last_requested_angular_velocity_degrees = 0.0
        self.tuning_values = replacement.tuning_values
        self.tuning_notice = None
        self.manual_angular_velocity_degrees = 0.0
        self.pose_samples.clear()
        self.last_pose_received_at = None
        self.recovery_reason = None
        self.recovery_stage = None
        self.metrics_warming = True
        self.warm_validation_pending = False
        self.presentation_failure = None

    def _remember_active_tuning(self) -> InteractiveTuning | None:
        tuning = self.manager.solver.interactive_tuning()
        if tuning is not None:
            self.tuning_values[self.manager.solver.info.id] = tuning.value
        return tuning

    def _apply_saved_tuning(self) -> InteractiveTuning | None:
        solver = self.manager.solver
        value = self.tuning_values.get(solver.info.id)
        tuning = solver.interactive_tuning()
        if value is not None and tuning is not None:
            tuning = solver.apply_interactive_tuning(value)
        if tuning is not None:
            self.tuning_values[solver.info.id] = tuning.value
        return tuning

    def adjust_solver_tuning(self, delta: float) -> bool:
        """Adjust the active solver's pedagogically useful live parameter."""
        solver = self.manager.solver
        tuning = solver.adjust_interactive_tuning(-1 if delta < 0.0 else 1)
        if tuning is not None:
            self.tuning_values[solver.info.id] = tuning.value
            self.tuning_notice = None
            return True
        self.tuning_notice = "no adjustable tuning"
        return False

    def set_reynolds(self, reynolds: float) -> None:
        selected = float(np.clip(reynolds, 50.0, 100_000.0))
        self.manager.set_reynolds(selected)

    def adjust_reynolds(self, decades: float) -> None:
        self.set_reynolds(self.manager.reynolds * 10.0**decades)

    def reset_reynolds(self) -> None:
        self.set_reynolds(self.scenario.reynolds)

    def toggle_vorticity(self) -> bool:
        self.show_vorticity = not self.show_vorticity
        if self.show_vorticity:
            try:
                self._refresh_diagnostics(force_vorticity=True)
            except Exception as error:
                self._pause_for_presentation_failure("diagnostic", error)
            else:
                self.presentation.diagnostic_elapsed = 0.0
                self.presentation_failure = None
        return self.show_vorticity

    def toggle_diagnostics(self) -> DiagnosticMode:
        self.presentation.diagnostic_mode = (
            "every-step"
            if self.presentation.diagnostic_mode == "cadenced"
            else "cadenced"
        )
        try:
            self._refresh_diagnostics()
        except Exception as error:
            self._pause_for_presentation_failure("diagnostic", error)
        else:
            self.presentation.diagnostic_elapsed = 0.0
            self.presentation_failure = None
        return self.presentation.diagnostic_mode

    def toggle_crop(self) -> bool:
        if viewer_bounds(self.scenario, cropped=True) == viewer_bounds(
            self.scenario,
            cropped=False,
        ):
            return False
        self.presentation.crop_enabled = not self.presentation.crop_enabled
        return self.presentation.crop_enabled

    def toggle_tracer_mode(self) -> str:
        return self.tracers.toggle_mode()

    def status(self) -> str:
        solver = self.manager.solver
        diagnostics = self.last_diagnostics
        report = self.last_report
        substeps = 0 if report is None else report.substeps
        speed = 0.0 if report is None else report.max_speed
        tuning = solver.interactive_tuning()
        tuning_display = (
            f"  {tuning.label}={tuning.display_value}" if tuning is not None else ""
        )
        effective_reynolds = (
            f"  Re_eff={diagnostics.values.get('effective_reynolds', 0.0):.0f}"
            if diagnostics is not None and "effective_reynolds" in diagnostics.values
            else ""
        )
        warning = ""
        if self.manager.last_import is not None:
            warning = "  warm-import transient"
        if self.recovery_notice is not None:
            warning = f"  recovered={self.recovery_notice}"
        if self.presentation_failure is not None:
            warning = f"  presentation-error={self.presentation_failure}"
        motion_mode = "  motion=pose-only" if self.pose_only_drag else ""
        recovery_epoch = (
            f"  recovery_epoch={self.recovery_count}" if self.recovery_count else ""
        )
        warming = self.metrics_warming or diagnostics is None or report is None
        energy = 0.0 if diagnostics is None else diagnostics.values.get("kinetic_energy", 0.0)
        enstrophy = 0.0 if diagnostics is None else diagnostics.values.get("enstrophy", 0.0)
        divergence = 0.0 if diagnostics is None else diagnostics.values.get("divergence_l2", 0.0)
        leakage = 0.0 if diagnostics is None else diagnostics.values.get("solid_leakage", 0.0)
        paused = "  PAUSED" if self.paused else ""
        control = self.control(self.scenario.output_dt)
        measurements = (
            "step=   —/s  sim/wall=   —  sub=—  max|u|=   —  E=—  Ω=—  div=—  leak=—"
            if warming
            else (
                f"step={self.solver_steps_per_second:4.1f}/s  "
                f"sim/wall={self.simulated_seconds_per_wall_second:4.2f}  "
                f"sub={substeps}  max|u|={speed:4.2f}  "
                f"E={energy:.3f}  Ω={enstrophy:.3f}  "
                f"div={divergence:.2e}  leak={leakage:.2e}"
            )
        )
        return (
            f"{solver.info.display_name}  t={self.time:6.2f}  "
            f"AoA={control.angle_degrees:5.1f}°  "
            f"Re={self.manager.reynolds:7.0f}  rate={self.playback_rate:4.2f}x  "
            f"{measurements}  "
            f"tracers={self.tracers.mode}  vort={'on' if self.show_vorticity else 'off'}"
            f"  view={'cropped' if self.crop_enabled else 'full'}"
            f"  diag={self.presentation.diagnostic_mode}"
            f"{tuning_display}{effective_reynolds}{motion_mode}{recovery_epoch}{paused}{warning}"
            f"{f'  tune={self.tuning_notice}' if self.tuning_notice is not None else ''}"
        )

    @property
    def available_solvers(self) -> tuple[str, ...]:
        return solver_ids()


def run_viewer(scenario: Scenario, initial_solver: str = "stable-fluids") -> None:
    from foilbench_py.viewer.gl_adapter import run_gl_window

    run_gl_window(ViewerModel.create(scenario, initial_solver))
