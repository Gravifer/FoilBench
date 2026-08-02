"""Single-owner simulation worker and immutable render snapshots."""

from collections import deque
from dataclasses import dataclass
from threading import Condition, Thread, current_thread
from time import perf_counter
from typing import Literal

from foilbench_py.core.geometry import NacaFoil
from foilbench_py.core.models import NumericalFailure, Scenario
from foilbench_py.types import PathVertices, ScalarField, TracerPositions
from foilbench_py.viewer.app import ViewerModel

type CommandKind = Literal[
    "toggle_pause",
    "reset",
    "switch_solver",
    "set_angle",
    "release_angle",
    "adjust_tuning",
    "adjust_reynolds",
    "reset_reynolds",
    "toggle_vorticity",
    "toggle_tracer",
    "toggle_crop",
    "shutdown",
]


@dataclass(frozen=True, slots=True)
class TimestampedPose:
    angle_degrees: float
    timestamp: float


@dataclass(frozen=True, slots=True)
class ViewerCommand:
    sequence: int
    kind: CommandKind
    value: float | str | TimestampedPose | None = None


@dataclass(frozen=True, slots=True)
class ViewerSnapshot:
    revision: int
    applied_command: int
    simulation_time: float
    angle_degrees: float
    status: str
    positions: TracerPositions
    path_segments: PathVertices
    vorticity: ScalarField | None
    vorticity_revision: int
    show_vorticity: bool
    crop_enabled: bool
    failure: str | None = None


class SimulationWorker:
    _FAILURE_WINDOW_SECONDS = 5.0
    _FAILURE_LIMIT = 3

    def __init__(self, model: ViewerModel, maximum_steps_per_second: float = 60.0) -> None:
        if maximum_steps_per_second <= 0.0:
            raise ValueError("maximum_steps_per_second must be positive")
        self._model = model
        self._step_interval = 1.0 / maximum_steps_per_second
        self._condition = Condition()
        self._commands: deque[ViewerCommand] = deque()
        self._next_command = 1
        self._stop_requested = False
        self._accepting_commands = True
        self._thread: Thread | None = None
        self._failure: str | None = None
        self._recovery_pending = False
        self._recent_failures: deque[float] = deque()
        self._snapshot = self._build_snapshot(0, 0)

    @property
    def scenario(self) -> Scenario:
        return self._model.scenario

    @property
    def geometry(self) -> NacaFoil:
        return self._model.geometry

    @property
    def maximum_line_vertices(self) -> int:
        history = self._model.tracers.history
        return (history.shape[0] - 1) * history.shape[1] * 2

    def start(self) -> None:
        with self._condition:
            if self._thread is not None:
                raise RuntimeError("simulation worker has already been started")
            if not self._accepting_commands:
                raise RuntimeError("simulation worker is closed")
            self._thread = Thread(
                target=self._run,
                name="foilbench-simulation",
                daemon=False,
            )
            self._thread.start()

    def close(self) -> None:
        with self._condition:
            thread = self._thread
            if thread is None:
                self._accepting_commands = False
                self._stop_requested = True
                return
            if not self._stop_requested:
                self._accepting_commands = False
                sequence = self._next_command
                self._next_command += 1
                self._commands.append(ViewerCommand(sequence, "shutdown"))
                self._condition.notify_all()
        if thread is not None and thread is not current_thread():
            thread.join()

    @property
    def is_alive(self) -> bool:
        thread = self._thread
        return thread is not None and thread.is_alive()

    def latest_snapshot(self) -> ViewerSnapshot:
        with self._condition:
            return self._snapshot

    def wait_for_revision(self, revision: int, timeout: float = 5.0) -> ViewerSnapshot:
        with self._condition:
            reached = self._condition.wait_for(
                lambda: self._snapshot.revision >= revision or not self.is_alive,
                timeout=timeout,
            )
            if not reached or self._snapshot.revision < revision:
                raise TimeoutError(f"simulation did not reach revision {revision}")
            return self._snapshot

    def wait_for_command(self, sequence: int, timeout: float = 5.0) -> ViewerSnapshot:
        with self._condition:
            reached = self._condition.wait_for(
                lambda: self._snapshot.applied_command >= sequence or not self.is_alive,
                timeout=timeout,
            )
            if not reached or self._snapshot.applied_command < sequence:
                raise TimeoutError(f"simulation did not apply command {sequence}")
            return self._snapshot

    def _enqueue(
        self,
        kind: CommandKind,
        value: float | str | TimestampedPose | None = None,
    ) -> int:
        with self._condition:
            if not self._accepting_commands:
                raise RuntimeError("simulation worker is closed")
            sequence = self._next_command
            self._next_command += 1
            command = ViewerCommand(sequence, kind, value)
            if kind == "set_angle" and self._commands and self._commands[-1].kind == kind:
                self._commands[-1] = command
            else:
                self._commands.append(command)
            self._condition.notify_all()
            return sequence

    def toggle_pause(self) -> int:
        return self._enqueue("toggle_pause")

    def reset(self) -> int:
        return self._enqueue("reset")

    def switch_solver(self, solver_id: str) -> int:
        return self._enqueue("switch_solver", solver_id)

    def set_angle(self, angle_degrees: float, timestamp: float | None = None) -> int:
        selected_time = perf_counter() if timestamp is None else timestamp
        return self._enqueue("set_angle", TimestampedPose(angle_degrees, selected_time))

    def release_angle(self) -> int:
        return self._enqueue("release_angle")

    def adjust_tuning(self, delta: float) -> int:
        return self._enqueue("adjust_tuning", delta)

    def adjust_reynolds(self, decades: float) -> int:
        return self._enqueue("adjust_reynolds", decades)

    def reset_reynolds(self) -> int:
        return self._enqueue("reset_reynolds")

    def toggle_vorticity(self) -> int:
        return self._enqueue("toggle_vorticity")

    def toggle_tracer_mode(self) -> int:
        return self._enqueue("toggle_tracer")

    def toggle_crop(self) -> int:
        return self._enqueue("toggle_crop")

    def _drain_commands(self) -> list[ViewerCommand]:
        with self._condition:
            commands = list(self._commands)
            self._commands.clear()
            return commands

    def _record_failure(self, now: float) -> int:
        self._recent_failures.append(now)
        cutoff = now - self._FAILURE_WINDOW_SECONDS
        while self._recent_failures and self._recent_failures[0] < cutoff:
            self._recent_failures.popleft()
        return len(self._recent_failures)

    def _clear_failure_history(self) -> None:
        self._recent_failures.clear()

    def _apply_command(self, command: ViewerCommand) -> None:
        if command.kind == "toggle_pause":
            self._model.paused = not self._model.paused
        elif command.kind == "reset":
            self._model.reset()
            self._failure = None
            self._recovery_pending = False
            self._clear_failure_history()
        elif command.kind == "switch_solver":
            if not isinstance(command.value, str):
                raise TypeError("switch_solver requires a solver id")
            self._model.switch_solver(command.value)
            self._failure = None
            self._recovery_pending = False
            self._clear_failure_history()
        elif command.kind == "set_angle":
            if not isinstance(command.value, TimestampedPose):
                raise TypeError("set_angle requires a timestamped pose")
            self._model.set_angle(command.value.angle_degrees, command.value.timestamp)
        elif command.kind == "release_angle":
            self._model.release_angle()
        elif command.kind == "adjust_tuning":
            if not isinstance(command.value, (int, float)):
                raise TypeError("adjust_tuning requires a numeric delta")
            self._model.adjust_solver_tuning(float(command.value))
        elif command.kind == "adjust_reynolds":
            if not isinstance(command.value, (int, float)):
                raise TypeError("adjust_reynolds requires a numeric logarithmic step")
            self._model.adjust_reynolds(float(command.value))
            self._recovery_pending = False
            self._clear_failure_history()
        elif command.kind == "reset_reynolds":
            self._model.reset_reynolds()
            self._recovery_pending = False
            self._clear_failure_history()
        elif command.kind == "toggle_vorticity":
            self._model.toggle_vorticity()
        elif command.kind == "toggle_tracer":
            self._model.toggle_tracer_mode()
        elif command.kind == "toggle_crop":
            self._model.toggle_crop()
        elif command.kind == "shutdown":
            self._stop_requested = True

    @staticmethod
    def _immutable_positions(array: TracerPositions) -> TracerPositions:
        copied = array.copy()
        copied.setflags(write=False)
        return copied

    @staticmethod
    def _immutable_paths(array: PathVertices) -> PathVertices:
        copied = array.copy()
        copied.setflags(write=False)
        return copied

    @staticmethod
    def _immutable_scalar(array: ScalarField) -> ScalarField:
        copied = array.copy()
        copied.setflags(write=False)
        return copied

    def _build_snapshot(self, revision: int, applied_command: int) -> ViewerSnapshot:
        previous = getattr(self, "_snapshot", None)
        source_vorticity = self._model.vorticity_display
        if source_vorticity is None:
            vorticity = None
        elif (
            isinstance(previous, ViewerSnapshot)
            and previous.vorticity_revision == self._model.vorticity_revision
        ):
            vorticity = previous.vorticity
        else:
            vorticity = self._immutable_scalar(source_vorticity)
        positions = self._immutable_positions(self._model.tracers.positions)
        path_segments = self._immutable_paths(self._model.tracers.path_segments())
        angle = self._model.control(self._model.scenario.output_dt).angle_degrees
        status = self._model.status()
        if self._failure is not None:
            status = f"{status}  worker-error={self._failure}"
        return ViewerSnapshot(
            revision=revision,
            applied_command=applied_command,
            simulation_time=self._model.time,
            angle_degrees=angle,
            status=status,
            positions=positions,
            path_segments=path_segments,
            vorticity=vorticity,
            vorticity_revision=self._model.vorticity_revision,
            show_vorticity=self._model.show_vorticity,
            crop_enabled=self._model.crop_enabled,
            failure=self._failure,
        )

    def _publish(self, applied_command: int) -> None:
        with self._condition:
            revision = self._snapshot.revision + 1
        snapshot = self._build_snapshot(revision, applied_command)
        with self._condition:
            self._snapshot = snapshot
            self._condition.notify_all()

    def _run(self) -> None:
        applied_command = 0
        while True:
            commands = self._drain_commands()

            for command in commands:
                try:
                    self._apply_command(command)
                except Exception as error:
                    self._failure = f"{type(error).__name__}: {error}"
                    self._model.paused = True
                applied_command = command.sequence

            if self._stop_requested:
                self._publish(applied_command)
                return

            if self._model.paused:
                if commands:
                    self._publish(applied_command)
                with self._condition:
                    if not self._stop_requested and not self._commands:
                        self._condition.wait()
                continue

            started = perf_counter()
            try:
                guarded_trial = self._model.pose_only_guarded_trial
                self._model.update(self._model.scenario.output_dt)
                if guarded_trial:
                    self._model.pose_only_guarded_trial = False
                self._recovery_pending = False
            except Exception as error:
                if not isinstance(error, NumericalFailure):
                    self._failure = f"{type(error).__name__}: {error}"
                    self._model.paused = True
                    self._publish(applied_command)
                    continue
                failure_count = self._record_failure(perf_counter())
                reynolds_is_modified = (
                    self._model.manager.reynolds != self._model.scenario.reynolds
                )
                pose_only_recovery = (
                    self._recovery_pending
                    and self._model.rapid_drag_attempted
                    and not self._model.pose_only_drag
                )
                reset_reynolds = not pose_only_recovery and reynolds_is_modified and (
                    self._recovery_pending or failure_count >= self._FAILURE_LIMIT
                )
                baseline_circuit_break = (
                    not reynolds_is_modified and failure_count >= self._FAILURE_LIMIT
                )
                guarded_trial_failed = self._model.pose_only_guarded_trial
                if (
                    guarded_trial_failed
                    or baseline_circuit_break
                    or (
                        self._recovery_pending
                        and not reset_reynolds
                        and not pose_only_recovery
                    )
                ):
                    self._failure = f"{type(error).__name__}: {error}"
                    self._model.paused = True
                else:
                    try:
                        if pose_only_recovery:
                            self._model.enable_pose_only_drag()
                        self._model.recover_solver(
                            error,
                            reset_reynolds=reset_reynolds,
                            post_import=self._model.warm_validation_pending,
                        )
                    except Exception as recovery_error:
                        self._failure = (
                            f"{type(error).__name__}: {error}; fresh restart failed: "
                            f"{type(recovery_error).__name__}: {recovery_error}"
                        )
                        self._model.paused = True
                    else:
                        self._failure = None
                        self._recovery_pending = True
                        if reset_reynolds or pose_only_recovery:
                            self._clear_failure_history()
            self._publish(applied_command)
            remaining = self._step_interval - (perf_counter() - started)
            if remaining > 0.0:
                with self._condition:
                    if not self._stop_requested and not self._commands:
                        self._condition.wait(timeout=remaining)
