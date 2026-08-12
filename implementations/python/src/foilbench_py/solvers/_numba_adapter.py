# pyright: reportMissingTypeStubs=false
# pyright: reportUnknownArgumentType=false
# pyright: reportUnknownMemberType=false
# pyright: reportUnknownVariableType=false
"""Typed boundary around kernels excluded from Jaxtyping's import hook."""

import numpy as np
from jaxtyping import Float

from foilbench_kernels.lbm import (
    moving_wall_stream_kernel,
    sponge_kernel,
    trt_collision_kernel,
)
from foilbench_kernels.pic import (
    gather_face_component_kernel,
    grid_to_particle_kernel,
    particle_to_grid_kernel,
    scatter_face_component_kernel,
)
from foilbench_py.types import (
    FaceVelocityX,
    FaceVelocityY,
    LatticePopulation,
    MaskField,
    ScalarField,
)


def _require_supported_float(array: np.ndarray, name: str) -> None:
    if array.dtype not in (np.dtype(np.float32), np.dtype(np.float64)):
        raise TypeError(f"{name} must use float32 or float64")


def particle_to_grid(
    positions: Float[np.ndarray, "particle 2"],
    velocities: Float[np.ndarray, "particle 2"],
    x0: float,
    y0: float,
    dx: float,
    dy: float,
    nx: int,
    ny: int,
    freestream: tuple[float, ...],
    periodic_x: bool = False,
    periodic_y: bool = False,
) -> Float[np.ndarray, "ny nx 2"]:
    if positions.ndim != 2 or positions.shape[1] != 2:
        raise ValueError("positions must have shape (particle, 2)")
    if velocities.shape != positions.shape:
        raise ValueError("velocities must match positions")
    _require_supported_float(positions, "positions")
    _require_supported_float(velocities, "velocities")
    if positions.dtype != velocities.dtype:
        raise TypeError("positions and velocities must use the same dtype")
    if dx <= 0.0 or dy <= 0.0 or nx <= 0 or ny <= 0:
        raise ValueError("grid spacing and resolution must be positive")
    return particle_to_grid_kernel(
        positions,
        velocities,
        x0,
        y0,
        dx,
        dy,
        nx,
        ny,
        freestream[0],
        freestream[1],
        periodic_x,
        periodic_y,
    )


def grid_to_particle(
    grid: Float[np.ndarray, "ny nx 2"],
    positions: Float[np.ndarray, "particle 2"],
    x0: float,
    y0: float,
    dx: float,
    dy: float,
    periodic_x: bool = False,
    periodic_y: bool = False,
) -> Float[np.ndarray, "particle 2"]:
    if grid.ndim != 3 or grid.shape[2] != 2:
        raise ValueError("grid must have shape (ny, nx, 2)")
    if grid.shape[0] == 0 or grid.shape[1] == 0:
        raise ValueError("grid axes must be nonempty")
    if positions.ndim != 2 or positions.shape[1] != 2:
        raise ValueError("positions must have shape (particle, 2)")
    _require_supported_float(grid, "grid")
    _require_supported_float(positions, "positions")
    if grid.dtype != positions.dtype:
        raise TypeError("grid and positions must use the same dtype")
    if dx <= 0.0 or dy <= 0.0:
        raise ValueError("grid spacing must be positive")
    return grid_to_particle_kernel(
        grid,
        positions,
        x0,
        y0,
        dx,
        dy,
        periodic_x,
        periodic_y,
    )


def particle_to_faces(
    positions: Float[np.ndarray, "particle 2"],
    velocities: Float[np.ndarray, "particle 2"],
    fallback_u: FaceVelocityX,
    fallback_v: FaceVelocityY,
    x0: float,
    y0: float,
    dx: float,
    dy: float,
    periodic_x: bool = False,
    periodic_y: bool = False,
) -> tuple[FaceVelocityX, FaceVelocityY, float]:
    if positions.ndim != 2 or positions.shape[1] != 2 or velocities.shape != positions.shape:
        raise ValueError("particle positions and velocities must have shape (particle, 2)")
    if fallback_u.shape != (fallback_v.shape[0] - 1, fallback_v.shape[1] + 1):
        raise ValueError("fallback arrays do not form a 2D MAC grid")
    u, unsupported_u = scatter_face_component_kernel(
        positions, velocities[:, 0], x0, y0, dx, dy,
        fallback_u.shape[1], fallback_u.shape[0], 0.0, -0.5,
        periodic_x, periodic_y, periodic_x, False, fallback_u,
    )
    v, unsupported_v = scatter_face_component_kernel(
        positions, velocities[:, 1], x0, y0, dx, dy,
        fallback_v.shape[1], fallback_v.shape[0], -0.5, 0.0,
        periodic_x, periodic_y, False, periodic_y, fallback_v,
    )
    unsupported = (unsupported_u + unsupported_v) / (u.size + v.size)
    return u, v, float(unsupported)


def faces_to_particle(
    u: FaceVelocityX,
    v: FaceVelocityY,
    positions: Float[np.ndarray, "particle 2"],
    x0: float,
    y0: float,
    dx: float,
    dy: float,
    periodic_x: bool = False,
    periodic_y: bool = False,
) -> Float[np.ndarray, "particle 2"]:
    if u.shape != (v.shape[0] - 1, v.shape[1] + 1):
        raise ValueError("face arrays do not form a 2D MAC grid")
    output = np.empty_like(positions)
    output[:, 0] = gather_face_component_kernel(
        u, positions, x0, y0, dx, dy, 0.0, -0.5,
        periodic_x, periodic_y, periodic_x, False,
    )
    output[:, 1] = gather_face_component_kernel(
        v, positions, x0, y0, dx, dy, -0.5, 0.0,
        periodic_x, periodic_y, False, periodic_y,
    )
    return output


def lbm_trt_collision(
    populations: LatticePopulation,
    omega_plus: float,
    omega_minus: float,
) -> tuple[ScalarField, LatticePopulation]:
    if populations.ndim != 3 or populations.shape[2] != 9:
        raise ValueError("D2Q9 populations must have shape (ny, nx, 9)")
    _require_supported_float(populations, "populations")
    if omega_plus <= 0.0 or omega_minus <= 0.0:
        raise ValueError("TRT relaxation rates must be positive")
    density, post_collision = trt_collision_kernel(
        populations,
        omega_plus,
        omega_minus,
    )
    return density, post_collision


def lbm_moving_wall_stream(
    post_collision: LatticePopulation,
    density: ScalarField,
    solid: MaskField,
    signed_distance: ScalarField,
    bounds: tuple[tuple[float, float], ...],
    spacing: tuple[float, float],
    pivot: tuple[float, float],
    angular_velocity_radians: float,
    lattice_velocity_scale: float,
) -> LatticePopulation:
    return moving_wall_stream_kernel(
        post_collision,
        density,
        solid,
        signed_distance,
        bounds[0][0],
        bounds[1][0],
        spacing[0],
        spacing[1],
        pivot[0],
        pivot[1],
        angular_velocity_radians,
        lattice_velocity_scale,
    )


def lbm_apply_sponge(
    populations: LatticePopulation,
    boundary_equilibrium: LatticePopulation,
    strength: ScalarField,
) -> None:
    sponge_kernel(populations, boundary_equilibrium, strength)
