"""Viewer-owned passive tracers and batched path history."""

from dataclasses import dataclass
from typing import Literal

import numpy as np
from jaxtyping import Bool

from foilbench_py.core.geometry import NacaFoil
from foilbench_py.core.models import ControlState, DomainSpec
from foilbench_py.core.protocol import FlowSolver
from foilbench_py.core.rng import PCG32
from foilbench_py.types import ParticleHistory, ParticleScalar, PointCloud

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
        return cls(domain, foil, positions, history, 0, rng, ages, lifetimes, mode)

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

        inside = self.foil.contains(self.positions[selected], angle_degrees)
        if np.any(inside):
            selected_indices = np.flatnonzero(selected)
            inside_indices = selected_indices[inside]
            points = self.positions[inside_indices]
            distance = self.foil.signed_distance(points, angle_degrees)
            normal = self.foil.normals(points, angle_degrees)
            self.positions[inside_indices] -= (distance[:, None] - 1.0e-4) * normal
        self.history[:, selected, :] = self.positions[selected][None, :, :]

    def toggle_mode(self) -> TracerMode:
        self.mode = "material" if self.mode == "display" else "display"
        if self.mode == "display":
            self.ages[:] = self.rng.random(self.ages.shape) * self.lifetimes
        return self.mode

    def update(self, solver: FlowSolver, control: ControlState, dt: float) -> None:
        velocity_0 = solver.sample_velocity(self.positions)
        midpoint = self.positions + 0.5 * dt * velocity_0
        velocity_mid = solver.sample_velocity(midpoint)
        self.positions += dt * velocity_mid
        self.ages += dt

        x0, x1 = self.domain.bounds[0]
        y0, y1 = self.domain.bounds[1]
        escaped = (
            (self.positions[:, 0] < x0)
            | (self.positions[:, 0] > x1)
            | (self.positions[:, 1] < y0)
            | (self.positions[:, 1] > y1)
        )
        self._respawn(escaped, throughout_domain=False, angle_degrees=control.angle_degrees)
        if self.mode == "display":
            expired = self.ages >= self.lifetimes
            self._respawn(expired, throughout_domain=True, angle_degrees=control.angle_degrees)

        inside = self.foil.contains(self.positions, control.angle_degrees)
        if np.any(inside):
            points = self.positions[inside]
            distance = self.foil.signed_distance(points, control.angle_degrees)
            normal = self.foil.normals(points, control.angle_degrees)
            self.positions[inside] -= (distance[:, None] - 1.0e-4) * normal

        self.history_index = (self.history_index + 1) % self.history.shape[0]
        self.history[self.history_index] = self.positions

    def ordered_history(self) -> ParticleHistory:
        return np.roll(self.history, -self.history_index - 1, axis=0)

    def path_segments(self, maximum_jump: float = 0.25) -> PointCloud:
        ordered = self.ordered_history()
        starts = ordered[:-1].reshape(-1, 2)
        ends = ordered[1:].reshape(-1, 2)
        valid = np.linalg.norm(ends - starts, axis=1) <= maximum_jump
        return np.stack((starts[valid], ends[valid]), axis=1).reshape(-1, 2)
