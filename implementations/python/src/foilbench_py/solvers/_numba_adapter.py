# pyright: reportMissingTypeStubs=false
# pyright: reportUnknownArgumentType=false
# pyright: reportUnknownMemberType=false
# pyright: reportUnknownVariableType=false
"""Typed boundary around kernels excluded from Jaxtyping's import hook."""

import numpy as np
from jaxtyping import Float

from foilbench_kernels.pic import grid_to_particle_kernel, particle_to_grid_kernel


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
    )


def grid_to_particle(
    grid: Float[np.ndarray, "ny nx 2"],
    positions: Float[np.ndarray, "particle 2"],
    x0: float,
    y0: float,
    dx: float,
    dy: float,
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
    return grid_to_particle_kernel(grid, positions, x0, y0, dx, dy)
