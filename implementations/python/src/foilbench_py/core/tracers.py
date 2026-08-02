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

    @classmethod
    def create(
        cls,
        domain: DomainSpec,
        foil: NacaFoil,
        count: int,
        history_length: int,
        seed: int,
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
        return cls(
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
        )

    def _respawn(
        self,
        selected: Bool[np.ndarray, " particle"],
        throughout_domain: bool,
        angle_degrees: float,
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

    def reseed_all(self, angle_degrees: float) -> None:
        """Redistribute every visible tracer and clear its path memory."""
        selected = np.ones(self.positions.shape[0], dtype=np.bool_)
        self._respawn(
            selected,
            throughout_domain=True,
            angle_degrees=angle_degrees,
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
        if "x" in self.domain.periodic_axes:
            self.positions[outside_x, 0] = x0 + np.mod(
                self.positions[outside_x, 0] - x0,
                x1 - x0,
            )
            wrapped |= outside_x
            outside_x = np.zeros_like(outside_x)
        if "y" in self.domain.periodic_axes:
            self.positions[outside_y, 1] = y0 + np.mod(
                self.positions[outside_y, 1] - y0,
                y1 - y0,
            )
            wrapped |= outside_y
            outside_y = np.zeros_like(outside_y)
        self.generations[wrapped] += 1
        escaped = outside_x | outside_y
        self._respawn(escaped, throughout_domain=False, angle_degrees=control.angle_degrees)
        if self.mode == "display":
            expired = self.ages >= self.lifetimes
            self._respawn(expired, throughout_domain=True, angle_degrees=control.angle_degrees)

        inside = self.foil.contains(self.positions, control.angle_degrees)
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
                )

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
