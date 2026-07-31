"""NACA four-digit geometry and moving-boundary queries."""

from dataclasses import dataclass

import numpy as np
from jaxtyping import Bool, Float

from foilbench_py.core.models import ControlState, DomainSpec, FoilSpec
from foilbench_py.types import PointCloud


@dataclass(frozen=True, slots=True)
class NacaFoil:
    spec: FoilSpec

    @property
    def maximum_camber(self) -> float:
        return int(self.spec.naca[0]) / 100.0

    @property
    def camber_position(self) -> float:
        return int(self.spec.naca[1]) / 10.0

    @property
    def thickness(self) -> float:
        return int(self.spec.naca[2:]) / 100.0

    @property
    def maximum_radius(self) -> float:
        vertical_extent = (
            self.maximum_camber + 0.51 * self.thickness
        ) * self.spec.chord
        return float(np.hypot(0.75 * self.spec.chord, vertical_extent))

    def _to_local(self, points: PointCloud, angle_degrees: float) -> Float[np.ndarray, "point 2"]:
        angle = np.deg2rad(angle_degrees)
        cosine = np.cos(angle)
        sine = np.sin(angle)
        translated = points[:, :2] - np.asarray(self.spec.pivot[:2], dtype=points.dtype)
        local = np.empty_like(translated)
        local[:, 0] = cosine * translated[:, 0] + sine * translated[:, 1]
        local[:, 1] = -sine * translated[:, 0] + cosine * translated[:, 1]
        local[:, 0] += 0.25 * self.spec.chord
        return local

    def _from_local(
        self, points: Float[np.ndarray, "point 2"], angle_degrees: float
    ) -> Float[np.ndarray, "point 2"]:
        angle = np.deg2rad(angle_degrees)
        cosine = np.cos(angle)
        sine = np.sin(angle)
        shifted = points.copy()
        shifted[:, 0] -= 0.25 * self.spec.chord
        world = np.empty_like(shifted)
        world[:, 0] = cosine * shifted[:, 0] - sine * shifted[:, 1]
        world[:, 1] = sine * shifted[:, 0] + cosine * shifted[:, 1]
        world += np.asarray(self.spec.pivot[:2], dtype=points.dtype)
        return world

    def surfaces(
        self, x_local: Float[np.ndarray, " point"]
    ) -> tuple[
        Float[np.ndarray, " point"],
        Float[np.ndarray, " point"],
    ]:
        chord = self.spec.chord
        x = np.clip(x_local / chord, 0.0, 1.0)
        thickness = (
            5.0
            * self.thickness
            * chord
            * (
                0.2969 * np.sqrt(np.maximum(x, 0.0))
                - 0.1260 * x
                - 0.3516 * x**2
                + 0.2843 * x**3
                - 0.1036 * x**4
            )
        )
        m = self.maximum_camber
        p = self.camber_position
        camber = np.zeros_like(x)
        if m > 0.0 and p > 0.0:
            forward = x < p
            camber[forward] = m / p**2 * (2.0 * p * x[forward] - x[forward] ** 2)
            camber[~forward] = (
                m / (1.0 - p) ** 2 * ((1.0 - 2.0 * p) + 2.0 * p * x[~forward] - x[~forward] ** 2)
            )
            camber *= chord
        return camber + thickness, camber - thickness

    def signed_distance(
        self, points: PointCloud, angle_degrees: float
    ) -> Float[np.ndarray, " point"]:
        local = self._to_local(points, angle_degrees)
        upper, lower = self.surfaces(local[:, 0])
        x = local[:, 0]
        y = local[:, 1]
        inside_x = (x >= 0.0) & (x <= self.spec.chord)
        vertical_outside = np.maximum(y - upper, lower - y)
        vertical_inside_distance = -np.minimum(upper - y, y - lower)
        vertical = np.where((y <= upper) & (y >= lower), vertical_inside_distance, vertical_outside)
        dx = np.maximum(-x, x - self.spec.chord)
        outside_x = np.maximum(dx, 0.0)
        distance = np.where(inside_x, vertical, np.hypot(outside_x, np.maximum(vertical, 0.0)))
        return np.asarray(distance, dtype=points.dtype)

    def normals(self, points: PointCloud, angle_degrees: float) -> Float[np.ndarray, "point 2"]:
        epsilon = max(self.spec.chord * 1.0e-4, 1.0e-6)
        offset_x = np.asarray([epsilon, 0.0], dtype=points.dtype)
        offset_y = np.asarray([0.0, epsilon], dtype=points.dtype)
        dx = self.signed_distance(points + offset_x, angle_degrees) - self.signed_distance(
            points - offset_x, angle_degrees
        )
        dy = self.signed_distance(points + offset_y, angle_degrees) - self.signed_distance(
            points - offset_y, angle_degrees
        )
        gradients = np.stack((dx, dy), axis=1)
        lengths = np.linalg.norm(gradients, axis=1, keepdims=True)
        ambiguous = lengths[:, 0] < epsilon
        if np.any(ambiguous):
            angle = np.deg2rad(angle_degrees)
            gradients[ambiguous, 0] = -np.sin(angle)
            gradients[ambiguous, 1] = np.cos(angle)
            lengths[ambiguous, 0] = 1.0
        return gradients / np.maximum(lengths, epsilon)

    def contains(self, points: PointCloud, angle_degrees: float) -> Bool[np.ndarray, " point"]:
        return self.signed_distance(points, angle_degrees) <= 0.0

    def wall_velocity(
        self, points: PointCloud, control: ControlState
    ) -> Float[np.ndarray, "point 2"]:
        relative = points[:, :2] - np.asarray(self.spec.pivot[:2], dtype=points.dtype)
        omega = np.deg2rad(control.angular_velocity_degrees)
        velocity = np.empty_like(relative)
        velocity[:, 0] = -omega * relative[:, 1]
        velocity[:, 1] = omega * relative[:, 0]
        return velocity

    def outline(
        self, angle_degrees: float, samples: int = 256, dtype: np.dtype[np.floating] | None = None
    ) -> Float[np.ndarray, "point 2"]:
        selected_dtype = dtype or np.dtype(np.float32)
        beta = np.linspace(0.0, np.pi, samples // 2, dtype=selected_dtype)
        x = self.spec.chord * 0.5 * (1.0 - np.cos(beta))
        upper, lower = self.surfaces(x)
        local = np.concatenate(
            (
                np.stack((x, upper), axis=1),
                np.stack((x[::-1], lower[::-1]), axis=1),
            ),
            axis=0,
        )
        return self._from_local(local, angle_degrees)

    def mask(self, domain: DomainSpec, angle_degrees: float) -> Bool[np.ndarray, "ny nx"]:
        points = cell_centers(domain)
        return self.contains(points.reshape(-1, 2), angle_degrees).reshape(domain.ny, domain.nx)


def cell_centers(domain: DomainSpec) -> Float[np.ndarray, "ny nx 2"]:
    x0, x1 = domain.bounds[0]
    y0, y1 = domain.bounds[1]
    x = np.linspace(x0 + 0.5 * domain.dx, x1 - 0.5 * domain.dx, domain.nx)
    y = np.linspace(y0 + 0.5 * domain.dy, y1 - 0.5 * domain.dy, domain.ny)
    xx, yy = np.meshgrid(x, y)
    return np.stack((xx, yy), axis=-1)
