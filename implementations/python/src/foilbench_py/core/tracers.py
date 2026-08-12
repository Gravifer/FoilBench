"""Viewer-owned passive tracers and batched path history."""

from dataclasses import dataclass
from typing import Literal

import numpy as np
from jaxtyping import Bool

from foilbench_py.core.geometry import NacaFoil
from foilbench_py.core.models import ControlState, DomainSpec
from foilbench_py.core.protocol import FlowSolver
from foilbench_py.core.rng import PCG32
from foilbench_py.types import (
    ParticleGeneration,
    ParticleGenerationHistory,
    ParticleHistory,
    ParticleScalar,
    PointCloud,
)

type TracerMode = Literal["display", "material"]
type TracerRecycleReason = Literal[
    "boundary_exit",
    "lifetime_expiry",
    "invalid_collision",
    "forced_recovery",
    "scenario_reset",
    "periodic_wrap",
]


@dataclass(slots=True)
class TracerSystem:
    domain: DomainSpec
    foil: NacaFoil
    positions: PointCloud
    history: ParticleHistory
    history_index: int
    rng: PCG32
    ages: ParticleScalar
    lifetimes: ParticleScalar
    generations: ParticleGeneration
    history_generations: ParticleGenerationHistory
    mode: TracerMode
    recycle_counters: dict[TracerRecycleReason, int]

    @classmethod
    def create(
        cls,
        domain: DomainSpec,
        foil: NacaFoil,
        count: int,
        history_length: int,
        seed: int,
        angle_degrees: float,
        mode: TracerMode = "display",
    ) -> "TracerSystem":
        rng = PCG32(seed, stream=97)
        x0, x1 = domain.bounds[0]
        y0, y1 = domain.bounds[1]
        positions = np.empty((count, 2), dtype=np.float32)
        positions[:, 0] = rng.uniform(x0, x1, (count,))
        positions[:, 1] = rng.uniform(y0, y1, (count,))
        history = np.repeat(positions[None, :, :], history_length, axis=0)
        lifetimes = 3.0 + 4.0 * rng.random((count,))
        ages = rng.random((count,)) * lifetimes
        generations = np.zeros((count,), dtype=np.int64)
        history_generations = np.zeros((history_length, count), dtype=np.int64)
        tracers = cls(
            domain,
            foil,
            positions,
            history,
            0,
            rng,
            ages,
            lifetimes,
            generations,
            history_generations,
            mode,
            {
                "boundary_exit": 0,
                "lifetime_expiry": 0,
                "invalid_collision": 0,
                "forced_recovery": 0,
                "scenario_reset": 0,
                "periodic_wrap": 0,
            },
        )
        inside = foil.contains(tracers.positions, angle_degrees)
        tracers._respawn(
            inside,
            throughout_domain=True,
            angle_degrees=angle_degrees,
        )
        # Initial placement is generation zero even when an unlucky sample had
        # to be moved out of the authoritative foil pose.
        tracers.generations[:] = 0
        tracers.history_generations[:] = 0
        return tracers

    def _respawn(
        self,
        selected: Bool[np.ndarray, " particle"],
        throughout_domain: bool,
        angle_degrees: float,
        reason: TracerRecycleReason | None = None,
    ) -> None:
        count = int(np.count_nonzero(selected))
        if count == 0:
            return
        x0, x1 = self.domain.bounds[0]
        y0, y1 = self.domain.bounds[1]
        if throughout_domain:
            self.positions[selected, 0] = self.rng.uniform(x0, x1, (count,))
        else:
            self.positions[selected, 0] = x0 + self.rng.uniform(0.0, 0.5 * self.domain.dx, (count,))
        self.positions[selected, 1] = self.rng.uniform(y0, y1, (count,))
        self.ages[selected] = 0.0
        self.lifetimes[selected] = 3.0 + 4.0 * self.rng.random((count,))
        self.generations[selected] += 1
        if reason is not None:
            self.recycle_counters[reason] += count

        inside = self.foil.contains(self.positions[selected], angle_degrees)
        if np.any(inside):
            selected_indices = np.flatnonzero(selected)
            inside_indices = selected_indices[inside]
            points = self.positions[inside_indices]
            distance = self.foil.signed_distance(points, angle_degrees)
            normal = self.foil.normals(points, angle_degrees)
            self.positions[inside_indices] -= (distance[:, None] - 1.0e-4) * normal
        self.history[:, selected, :] = self.positions[selected][None, :, :]
        self.history_generations[:, selected] = self.generations[selected][None, :]

    def toggle_mode(self) -> TracerMode:
        self.mode = "material" if self.mode == "display" else "display"
        if self.mode == "display":
            self.ages[:] = self.rng.random(self.ages.shape) * self.lifetimes
        return self.mode

    def reseed_all(
        self,
        angle_degrees: float,
        reason: Literal["forced_recovery", "scenario_reset"] = "forced_recovery",
    ) -> None:
        """Redistribute every visible tracer and clear its path memory."""
        selected = np.ones(self.positions.shape[0], dtype=np.bool_)
        self._respawn(
            selected,
            throughout_domain=True,
            angle_degrees=angle_degrees,
            reason=reason,
        )
        self.ages[:] = self.rng.random(self.ages.shape) * self.lifetimes
        self.history_index = 0

    def update(self, solver: FlowSolver, control: ControlState, dt: float) -> None:
        velocity_0 = solver.sample_velocity(self.positions)
        midpoint = self.positions + 0.5 * dt * velocity_0
        velocity_mid = solver.sample_velocity(midpoint)
        self.positions += dt * velocity_mid
        self.ages += dt

        x0, x1 = self.domain.bounds[0]
        y0, y1 = self.domain.bounds[1]
        outside_x = (self.positions[:, 0] < x0) | (self.positions[:, 0] > x1)
        outside_y = (self.positions[:, 1] < y0) | (self.positions[:, 1] > y1)
        wrapped = np.zeros(self.positions.shape[0], dtype=np.bool_)
        escaped_x = (
            outside_x
            if "x" not in self.domain.periodic_axes
            else np.zeros_like(outside_x)
        )
        escaped_y = (
            outside_y
            if "y" not in self.domain.periodic_axes
            else np.zeros_like(outside_y)
        )
        escaped = escaped_x | escaped_y
        if "x" in self.domain.periodic_axes:
            selected = outside_x & ~escaped
            self.positions[selected, 0] = x0 + np.mod(
                self.positions[selected, 0] - x0,
                x1 - x0,
            )
            wrapped |= selected
        if "y" in self.domain.periodic_axes:
            selected = outside_y & ~escaped
            self.positions[selected, 1] = y0 + np.mod(
                self.positions[selected, 1] - y0,
                y1 - y0,
            )
            wrapped |= selected
        self._respawn(
            escaped,
            throughout_domain=False,
            angle_degrees=control.angle_degrees,
            reason="boundary_exit",
        )

        active = ~escaped
        inside = self.foil.contains(self.positions, control.angle_degrees) & active
        if np.any(inside):
            inside_indices = np.flatnonzero(inside)
            points = self.positions[inside_indices]
            distance = self.foil.signed_distance(points, control.angle_degrees)
            normal = self.foil.normals(points, control.angle_degrees)
            normal_norm = np.linalg.norm(normal, axis=1)
            shallow_limit = 0.5 * min(self.domain.dx, self.domain.dy)
            projectable = (
                (distance >= -shallow_limit)
                & np.isfinite(distance)
                & np.isfinite(normal).all(axis=1)
                & (normal_norm > 1.0e-8)
            )
            if np.any(projectable):
                selected_indices = inside_indices[projectable]
                selected_distance = distance[projectable]
                selected_normal = normal[projectable] / normal_norm[projectable, None]
                self.positions[selected_indices] -= (
                    selected_distance[:, None] - 1.0e-4
                ) * selected_normal
            if np.any(~projectable):
                respawn = np.zeros(self.positions.shape[0], dtype=np.bool_)
                respawn[inside_indices[~projectable]] = True
                self._respawn(
                    respawn,
                    throughout_domain=self.mode == "display",
                    angle_degrees=control.angle_degrees,
                    reason="invalid_collision",
                )
                active &= ~respawn

        if self.mode == "display":
            expired = (self.ages >= self.lifetimes) & active
            self._respawn(
                expired,
                throughout_domain=True,
                angle_degrees=control.angle_degrees,
                reason="lifetime_expiry",
            )
            active &= ~expired

        committed_wrap = wrapped & active
        self.generations[committed_wrap] += 1
        self.recycle_counters["periodic_wrap"] += int(np.count_nonzero(committed_wrap))

        self.history_index = (self.history_index + 1) % self.history.shape[0]
        self.history[self.history_index] = self.positions
        self.history_generations[self.history_index] = self.generations

    def ordered_history(self) -> ParticleHistory:
        return np.roll(self.history, -self.history_index - 1, axis=0)

    def path_segments(self) -> PointCloud:
        ordered = self.ordered_history()
        ordered_generations = np.roll(
            self.history_generations,
            -self.history_index - 1,
            axis=0,
        )
        starts = ordered[:-1].reshape(-1, 2)
        ends = ordered[1:].reshape(-1, 2)
        valid = (ordered_generations[:-1] == ordered_generations[1:]).reshape(-1)
        return np.stack((starts[valid], ends[valid]), axis=1).reshape(-1, 2)
