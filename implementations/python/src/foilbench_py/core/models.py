"""Immutable model objects shared by solvers, viewers, and benchmarks."""

from dataclasses import dataclass, field
from typing import Literal

import numpy as np
from jaxtyping import Float

from foilbench_py.types import ScalarVolume, VelocityVolume

Precision = Literal["float32", "float64"]
AxisName = Literal["x", "y", "z"]


@dataclass(frozen=True, slots=True)
class DomainSpec:
    dimension: Literal[2, 3]
    bounds: tuple[tuple[float, float], ...]
    resolution: tuple[int, ...]
    periodic_axes: tuple[AxisName, ...] = ()

    def __post_init__(self) -> None:
        if len(self.bounds) != self.dimension or len(self.resolution) != self.dimension:
            raise ValueError("bounds and resolution must match dimension")
        if any(upper <= lower for lower, upper in self.bounds):
            raise ValueError("each domain upper bound must exceed its lower bound")
        if any(size < 4 for size in self.resolution):
            raise ValueError("each resolution axis must contain at least four cells")

    @property
    def nx(self) -> int:
        return self.resolution[0]

    @property
    def ny(self) -> int:
        return self.resolution[1]

    @property
    def nz(self) -> int:
        return 1 if self.dimension == 2 else self.resolution[2]

    @property
    def dx(self) -> float:
        return (self.bounds[0][1] - self.bounds[0][0]) / self.nx

    @property
    def dy(self) -> float:
        return (self.bounds[1][1] - self.bounds[1][0]) / self.ny


@dataclass(frozen=True, slots=True)
class FoilSpec:
    naca: str
    chord: float
    pivot: tuple[float, ...]

    def __post_init__(self) -> None:
        if len(self.naca) != 4 or not self.naca.isdigit():
            raise ValueError("NACA code must contain exactly four digits")
        if self.chord <= 0.0:
            raise ValueError("foil chord must be positive")


@dataclass(frozen=True, slots=True)
class ControlKeyframe:
    time: float
    angle_degrees: float


@dataclass(frozen=True, slots=True)
class ControlState:
    time: float
    angle_degrees: float
    angular_velocity_degrees: float


@dataclass(frozen=True, slots=True)
class Scenario:
    schema_version: int
    id: str
    domain: DomainSpec
    reynolds: float
    freestream: tuple[float, ...]
    foil: FoilSpec
    controls: tuple[ControlKeyframe, ...]
    duration: float
    output_dt: float
    precision: Precision
    seed: int
    solver_options: dict[str, object] = field(default_factory=lambda: {})

    def __post_init__(self) -> None:
        if self.domain.dimension != len(self.freestream):
            raise ValueError("freestream dimensionality must match the domain")
        if len(self.foil.pivot) != self.domain.dimension:
            raise ValueError("foil pivot dimensionality must match the domain")
        if not self.controls:
            raise ValueError("a scenario needs at least one control keyframe")
        if any(b.time < a.time for a, b in zip(self.controls, self.controls[1:], strict=False)):
            raise ValueError("control keyframes must be sorted by time")
        if self.reynolds <= 0.0 or self.duration <= 0.0 or self.output_dt <= 0.0:
            raise ValueError("Reynolds number, duration, and output_dt must be positive")

    @property
    def dtype(self) -> np.dtype[np.floating]:
        return np.dtype(np.float32 if self.precision == "float32" else np.float64)

    @property
    def reference_speed(self) -> float:
        freestream_speed = float(np.linalg.norm(np.asarray(self.freestream, dtype=np.float64)))
        initial_condition = str(self.solver_options.get("initial_condition", "freestream"))
        prescribed_speed = 1.0 if initial_condition in ("taylor-green", "poiseuille") else 0.0
        return max(freestream_speed, prescribed_speed, 1.0e-12)

    def control_at(self, time: float) -> ControlState:
        if len(self.controls) == 1 or time <= self.controls[0].time:
            return ControlState(time, self.controls[0].angle_degrees, 0.0)
        if time >= self.controls[-1].time:
            return ControlState(time, self.controls[-1].angle_degrees, 0.0)
        for left, right in zip(self.controls, self.controls[1:], strict=False):
            if left.time <= time <= right.time:
                duration = right.time - left.time
                if duration <= 0.0:
                    return ControlState(time, right.angle_degrees, 0.0)
                linear = (time - left.time) / duration
                smooth = linear * linear * (3.0 - 2.0 * linear)
                angle = left.angle_degrees + smooth * (right.angle_degrees - left.angle_degrees)
                derivative = (
                    6.0
                    * linear
                    * (1.0 - linear)
                    * (right.angle_degrees - left.angle_degrees)
                    / duration
                )
                return ControlState(time, angle, derivative)
        return ControlState(time, self.controls[-1].angle_degrees, 0.0)


@dataclass(frozen=True, slots=True)
class SolverInfo:
    id: str
    display_name: str
    dimensions: tuple[int, ...]
    supports_moving_boundary: bool
    acceleration: str


@dataclass(frozen=True, slots=True)
class StepReport:
    requested_dt: float
    advanced_dt: float
    substeps: int
    max_speed: float
    warnings: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class ImportReport:
    source_solver: str
    destination_solver: str
    discarded_state: tuple[str, ...]
    warnings: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class Diagnostics:
    values: dict[str, float]
    warnings: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class CanonicalFlowState:
    schema_version: int
    dimension: Literal[2, 3]
    bounds: tuple[tuple[float, float], ...]
    resolution: tuple[int, ...]
    periodic_axes: tuple[AxisName, ...]
    time: float
    precision: Precision
    angle_degrees: float
    angular_velocity_degrees: float
    source_language: str
    source_solver: str
    velocity: VelocityVolume
    density: ScalarVolume | None = None

    def __post_init__(self) -> None:
        expected_z = 1 if self.dimension == 2 else self.resolution[2]
        expected_shape = (expected_z, self.resolution[1], self.resolution[0], self.dimension)
        if self.velocity.shape != expected_shape:
            raise ValueError(
                f"velocity shape {self.velocity.shape} does not match {expected_shape}"
            )
        if self.density is not None and self.density.shape != expected_shape[:-1]:
            raise ValueError("density shape does not match the canonical grid")
        expected_dtype = np.dtype(self.precision)
        if self.velocity.dtype != expected_dtype:
            raise TypeError("velocity dtype does not match canonical precision")
        if self.density is not None and self.density.dtype != expected_dtype:
            raise TypeError("density dtype does not match canonical precision")
        if not np.isfinite(self.velocity).all():
            raise ValueError("canonical velocity contains non-finite values")


FloatScalar = Float[np.ndarray, ""]
