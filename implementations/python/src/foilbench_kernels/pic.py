# pyright: reportMissingTypeStubs=false
# pyright: reportUnknownArgumentType=false
# pyright: reportUnknownMemberType=false
# pyright: reportUnknownVariableType=false
"""Numba implementation details; call through foilbench_py.solvers._numba_adapter."""

import numba
import numpy as np


@numba.njit
def _weight(distance: float) -> float:
    absolute = abs(distance)
    if absolute < 0.5:
        return 0.75 - absolute * absolute
    if absolute < 1.5:
        difference = 1.5 - absolute
        return 0.5 * difference * difference
    return 0.0


@numba.njit
def particle_to_grid_kernel(
    positions: np.ndarray,
    velocities: np.ndarray,
    x0: float,
    y0: float,
    dx: float,
    dy: float,
    nx: int,
    ny: int,
    freestream_x: float,
    freestream_y: float,
) -> np.ndarray:
    weights = np.zeros((ny, nx), dtype=positions.dtype)
    momentum = np.zeros((ny, nx, 2), dtype=positions.dtype)
    for particle in range(positions.shape[0]):
        gx = (positions[particle, 0] - x0) / dx - 0.5
        gy = (positions[particle, 1] - y0) / dy - 0.5
        base_x = int(np.floor(gx - 0.5))
        base_y = int(np.floor(gy - 0.5))
        for offset_y in range(3):
            source_y = base_y + offset_y
            target_y = min(max(source_y, 0), ny - 1)
            wy = _weight(gy - source_y)
            for offset_x in range(3):
                source_x = base_x + offset_x
                target_x = min(max(source_x, 0), nx - 1)
                weight = wy * _weight(gx - source_x)
                weights[target_y, target_x] += weight
                momentum[target_y, target_x, 0] += weight * velocities[particle, 0]
                momentum[target_y, target_x, 1] += weight * velocities[particle, 1]
    grid = np.empty_like(momentum)
    for y in range(ny):
        for x in range(nx):
            if weights[y, x] > 1.0e-12:
                grid[y, x, 0] = momentum[y, x, 0] / weights[y, x]
                grid[y, x, 1] = momentum[y, x, 1] / weights[y, x]
            else:
                grid[y, x, 0] = freestream_x
                grid[y, x, 1] = freestream_y
    return grid


@numba.njit
def grid_to_particle_kernel(
    grid: np.ndarray,
    positions: np.ndarray,
    x0: float,
    y0: float,
    dx: float,
    dy: float,
) -> np.ndarray:
    velocities = np.empty_like(positions)
    ny, nx, _ = grid.shape
    for particle in range(positions.shape[0]):
        gx = (positions[particle, 0] - x0) / dx - 0.5
        gy = (positions[particle, 1] - y0) / dy - 0.5
        base_x = int(np.floor(gx - 0.5))
        base_y = int(np.floor(gy - 0.5))
        velocity_x = 0.0
        velocity_y = 0.0
        weight_sum = 0.0
        for offset_y in range(3):
            source_y = base_y + offset_y
            target_y = min(max(source_y, 0), ny - 1)
            wy = _weight(gy - source_y)
            for offset_x in range(3):
                source_x = base_x + offset_x
                target_x = min(max(source_x, 0), nx - 1)
                weight = wy * _weight(gx - source_x)
                velocity_x += weight * grid[target_y, target_x, 0]
                velocity_y += weight * grid[target_y, target_x, 1]
                weight_sum += weight
        inverse_weight = 1.0 / max(weight_sum, 1.0e-12)
        velocities[particle, 0] = velocity_x * inverse_weight
        velocities[particle, 1] = velocity_y * inverse_weight
    return velocities
