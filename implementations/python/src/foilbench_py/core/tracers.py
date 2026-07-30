"""Viewer-owned passive tracers and batched path history."""

from dataclasses import dataclass

import numpy as np

from foilbench_py.core.geometry import NacaFoil
from foilbench_py.core.models import ControlState, DomainSpec
from foilbench_py.core.protocol import FlowSolver
from foilbench_py.core.rng import PCG32
from foilbench_py.types import ParticleHistory, PointCloud


@dataclass(slots=True)
class TracerSystem:
    domain: DomainSpec
    foil: NacaFoil
    positions: PointCloud
    history: ParticleHistory
    history_index: int
    rng: PCG32

    @classmethod
    def create(
        cls,
        domain: DomainSpec,
        foil: NacaFoil,
        count: int,
        history_length: int,
        seed: int,
    ) -> "TracerSystem":
        rng = PCG32(seed, stream=97)
        x0, x1 = domain.bounds[0]
        y0, y1 = domain.bounds[1]
        positions = np.empty((count, 2), dtype=np.float32)
        positions[:, 0] = rng.uniform(x0, x1, (count,))
        positions[:, 1] = rng.uniform(y0, y1, (count,))
        history = np.repeat(positions[None, :, :], history_length, axis=0)
        return cls(domain, foil, positions, history, 0, rng)

    def update(self, solver: FlowSolver, control: ControlState, dt: float) -> None:
        velocity_0 = solver.sample_velocity(self.positions)
        midpoint = self.positions + 0.5 * dt * velocity_0
        velocity_mid = solver.sample_velocity(midpoint)
        self.positions += dt * velocity_mid

        x0, x1 = self.domain.bounds[0]
        y0, y1 = self.domain.bounds[1]
        escaped = (
            (self.positions[:, 0] < x0)
            | (self.positions[:, 0] > x1)
            | (self.positions[:, 1] < y0)
            | (self.positions[:, 1] > y1)
        )
        count = int(np.count_nonzero(escaped))
        if count:
            self.positions[escaped, 0] = x0 + self.rng.uniform(0.0, 0.5 * self.domain.dx, (count,))
            self.positions[escaped, 1] = self.rng.uniform(y0, y1, (count,))

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
