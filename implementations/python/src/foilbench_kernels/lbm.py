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


@numba.njit
def moving_wall_stream_kernel(
    post_collision: np.ndarray,
    density: np.ndarray,
    solid: np.ndarray,
    signed_distance: np.ndarray,
    x0: float,
    y0: float,
    dx: float,
    dy: float,
    pivot_x: float,
    pivot_y: float,
    angular_velocity_radians: float,
    lattice_velocity_scale: float,
) -> np.ndarray:
    ny, nx, _ = post_collision.shape
    streamed = np.zeros_like(post_collision)
    opposite = (0, 3, 4, 1, 2, 7, 8, 5, 6)
    cx = (0, 1, 0, -1, 0, 1, -1, -1, 1)
    cy = (0, 0, 1, 0, -1, 1, 1, -1, -1)
    weights = (4.0 / 9.0, 1.0 / 9.0, 1.0 / 9.0, 1.0 / 9.0, 1.0 / 9.0,
               1.0 / 36.0, 1.0 / 36.0, 1.0 / 36.0, 1.0 / 36.0)
    streamed[:, :, 0] = post_collision[:, :, 0]
    for y in range(ny):
        for x in range(nx):
            if solid[y, x]:
                continue
            for direction in range(1, 9):
                direction_x = cx[direction]
                direction_y = cy[direction]
                destination_x = (x + direction_x) % nx
                destination_y = (y + direction_y) % ny
                source_population = post_collision[y, x, direction]
                if not solid[destination_y, destination_x]:
                    streamed[destination_y, destination_x, direction] += source_population
                    continue
                denominator = max(
                    signed_distance[y, x] - signed_distance[destination_y, destination_x],
                    1.0e-12,
                )
                fraction = min(max(signed_distance[y, x] / denominator, 0.05), 1.0)
                opposite_direction = opposite[direction]
                if fraction < 0.5:
                    upstream_x = (x - direction_x) % nx
                    upstream_y = (y - direction_y) % ny
                    reflected = (
                        2.0 * fraction * source_population
                        + (1.0 - 2.0 * fraction)
                        * post_collision[upstream_y, upstream_x, direction]
                    )
                else:
                    reflected = source_population / (2.0 * fraction) + (
                        (2.0 * fraction - 1.0)
                        * post_collision[y, x, opposite_direction]
                        / (2.0 * fraction)
                    )
                wall_x = x0 + (x + 0.5) * dx + fraction * direction_x * dx
                wall_y = y0 + (y + 0.5) * dy + fraction * direction_y * dy
                wall_u = -angular_velocity_radians * (wall_y - pivot_y)
                wall_v = angular_velocity_radians * (wall_x - pivot_x)
                wall_projection = direction_x * wall_u + direction_y * wall_v
                reflected -= (
                    6.0
                    * weights[direction]
                    * density[y, x]
                    * wall_projection
                    * lattice_velocity_scale
                )
                streamed[y, x, opposite_direction] = reflected
    return streamed


@numba.njit
def sponge_kernel(
    populations: np.ndarray,
    boundary_equilibrium: np.ndarray,
    strength: np.ndarray,
) -> None:
    ny, nx, directions = populations.shape
    for y in range(ny):
        for x in range(nx):
            selected = strength[y, x]
            if selected == 0.0:
                continue
            retained = 1.0 - selected
            for direction in range(directions):
                populations[y, x, direction] = (
                    retained * populations[y, x, direction]
                    + selected * boundary_equilibrium[y, x, direction]
                )
