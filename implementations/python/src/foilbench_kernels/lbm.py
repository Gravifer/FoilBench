# pyright: reportMissingTypeStubs=false
# pyright: reportUnknownArgumentType=false
# pyright: reportUnknownMemberType=false
# pyright: reportUnknownVariableType=false
"""Compiled D2Q9 kernels; call through the typed solver adapter."""

import numba
import numpy as np


@numba.njit
def trt_collision_kernel(
    populations: np.ndarray,
    omega_plus: float,
    omega_minus: float,
) -> tuple[np.ndarray, np.ndarray]:
    ny, nx, _ = populations.shape
    density = np.empty((ny, nx), dtype=populations.dtype)
    post_collision = np.empty_like(populations)
    opposite = (0, 3, 4, 1, 2, 7, 8, 5, 6)
    cx = (0, 1, 0, -1, 0, 1, -1, -1, 1)
    cy = (0, 0, 1, 0, -1, 1, 1, -1, -1)
    weights = (4.0 / 9.0, 1.0 / 9.0, 1.0 / 9.0, 1.0 / 9.0, 1.0 / 9.0,
               1.0 / 36.0, 1.0 / 36.0, 1.0 / 36.0, 1.0 / 36.0)
    even_rate = 0.5 * (omega_plus + omega_minus)
    odd_rate = 0.5 * (omega_plus - omega_minus)

    for y in range(ny):
        for x in range(nx):
            rho = 0.0
            for direction in range(9):
                rho += populations[y, x, direction]
            density[y, x] = rho
            safe_density = max(rho, 1.0e-12)
            ux = (
                populations[y, x, 1]
                - populations[y, x, 3]
                + populations[y, x, 5]
                - populations[y, x, 6]
                - populations[y, x, 7]
                + populations[y, x, 8]
            ) / safe_density
            uy = (
                populations[y, x, 2]
                - populations[y, x, 4]
                + populations[y, x, 5]
                + populations[y, x, 6]
                - populations[y, x, 7]
                - populations[y, x, 8]
            ) / safe_density
            speed_squared = ux * ux + uy * uy
            for direction in range(9):
                projection = cx[direction] * ux + cy[direction] * uy
                equilibrium = rho * weights[direction] * (
                    1.0 + 3.0 * projection + 4.5 * projection * projection
                    - 1.5 * speed_squared
                )
                opposite_direction = opposite[direction]
                opposite_projection = (
                    cx[opposite_direction] * ux + cy[opposite_direction] * uy
                )
                opposite_equilibrium = rho * weights[opposite_direction] * (
                    1.0 + 3.0 * opposite_projection
                    + 4.5 * opposite_projection * opposite_projection
                    - 1.5 * speed_squared
                )
                delta = populations[y, x, direction] - equilibrium
                opposite_delta = (
                    populations[y, x, opposite_direction] - opposite_equilibrium
                )
                post_collision[y, x, direction] = (
                    populations[y, x, direction]
                    - even_rate * delta
                    - odd_rate * opposite_delta
                )
    return density, post_collision
