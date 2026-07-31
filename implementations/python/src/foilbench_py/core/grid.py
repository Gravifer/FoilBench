"""Shared 2D MAC-grid operations."""

from collections.abc import Callable

import numpy as np
from jaxtyping import Float

from foilbench_py.core._scipy_adapter import solve_masked_poisson
from foilbench_py.core.geometry import cell_centers
from foilbench_py.core.interpolation import sample_vector
from foilbench_py.core.models import DomainSpec
from foilbench_py.types import FaceVelocityX, FaceVelocityY, MaskField, VelocityField


def faces_to_cell(u: FaceVelocityX, v: FaceVelocityY) -> Float[np.ndarray, "ny nx 2"]:
    return np.stack(
        (0.5 * (u[:, :-1] + u[:, 1:]), 0.5 * (v[:-1, :] + v[1:, :])),
        axis=-1,
    )


def cell_to_faces(
    velocity: VelocityField,
) -> tuple[FaceVelocityX, FaceVelocityY]:
    ny, nx, _ = velocity.shape
    u = np.empty((ny, nx + 1), dtype=velocity.dtype)
    v = np.empty((ny + 1, nx), dtype=velocity.dtype)
    u[:, 1:-1] = 0.5 * (velocity[:, :-1, 0] + velocity[:, 1:, 0])
    u[:, 0] = velocity[:, 0, 0]
    u[:, -1] = velocity[:, -1, 0]
    v[1:-1, :] = 0.5 * (velocity[:-1, :, 1] + velocity[1:, :, 1])
    v[0, :] = velocity[0, :, 1]
    v[-1, :] = velocity[-1, :, 1]
    return u, v


def apply_domain_boundaries(
    u: FaceVelocityX,
    v: FaceVelocityY,
    domain: DomainSpec,
    freestream: tuple[float, ...],
    channel_walls: bool = False,
) -> None:
    ux = freestream[0]
    uy = freestream[1]
    if "x" in domain.periodic_axes:
        u[:, 0] = u[:, -2]
        u[:, -1] = u[:, 1]
        v[:, 0] = v[:, -2]
        v[:, -1] = v[:, 1]
    else:
        u[:, 0] = ux
        u[:, -1] = u[:, -2]
        v[:, 0] = uy
        v[:, -1] = v[:, -2]
    if "y" in domain.periodic_axes:
        v[0, :] = v[-2, :]
        v[-1, :] = v[1, :]
        u[0, :] = u[-2, :]
        u[-1, :] = u[1, :]
    elif channel_walls:
        v[0, :] = 0.0
        v[-1, :] = 0.0
        u[0, :] = 0.0
        u[-1, :] = 0.0
    else:
        v[0, :] = uy
        v[-1, :] = uy
        u[0, :] = ux
        u[-1, :] = ux


def enforce_solid_faces(
    u: FaceVelocityX,
    v: FaceVelocityY,
    solid: MaskField,
    wall_velocity: VelocityField,
) -> None:
    solid_u = np.zeros_like(u, dtype=np.bool_)
    solid_v = np.zeros_like(v, dtype=np.bool_)
    solid_u[:, 1:-1] = solid[:, :-1] | solid[:, 1:]
    solid_u[:, 0] = solid[:, 0]
    solid_u[:, -1] = solid[:, -1]
    solid_v[1:-1, :] = solid[:-1, :] | solid[1:, :]
    solid_v[0, :] = solid[0, :]
    solid_v[-1, :] = solid[-1, :]
    wall_u, wall_v = cell_to_faces(wall_velocity)
    u[solid_u] = wall_u[solid_u]
    v[solid_v] = wall_v[solid_v]


def project_faces(
    u: FaceVelocityX,
    v: FaceVelocityY,
    domain: DomainSpec,
    solid: MaskField,
    wall_velocity: VelocityField,
    freestream: tuple[float, ...],
    dt: float,
    channel_walls: bool = False,
    pressure_tolerance: float = 1.0e-5,
) -> tuple[FaceVelocityX, FaceVelocityY, int]:
    apply_domain_boundaries(u, v, domain, freestream, channel_walls)
    enforce_solid_faces(u, v, solid, wall_velocity)
    divergence = (u[:, 1:] - u[:, :-1]) / domain.dx + (v[1:, :] - v[:-1, :]) / domain.dy
    fluid = ~solid
    rhs = np.where(fluid, -divergence / max(dt, 1.0e-12), 0.0)
    pressure, info = solve_masked_poisson(
        rhs,
        fluid,
        domain.dx,
        domain.dy,
        domain.periodic_axes,
        pressure_tolerance,
    )
    u[:, 1:-1] -= dt * (pressure[:, 1:] - pressure[:, :-1]) / domain.dx
    v[1:-1, :] -= dt * (pressure[1:, :] - pressure[:-1, :]) / domain.dy
    apply_domain_boundaries(u, v, domain, freestream, channel_walls)
    enforce_solid_faces(u, v, solid, wall_velocity)
    return u, v, info


def implicit_diffuse(
    velocity: VelocityField,
    viscosity: float,
    dt: float,
    domain: DomainSpec,
    iterations: int = 12,
) -> VelocityField:
    if viscosity <= 0.0:
        return velocity.copy()
    ax = viscosity * dt / (domain.dx * domain.dx)
    ay = viscosity * dt / (domain.dy * domain.dy)
    denominator = 1.0 + 2.0 * ax + 2.0 * ay
    original = velocity.copy()
    result = velocity.copy()
    for _ in range(iterations):
        padded = np.pad(result, ((1, 1), (1, 1), (0, 0)), mode="edge")
        result = (
            original
            + ax * (padded[1:-1, :-2] + padded[1:-1, 2:])
            + ay * (padded[:-2, 1:-1] + padded[2:, 1:-1])
        ) / denominator
    return result


def advect_velocity(
    velocity: VelocityField,
    dt: float,
    domain: DomainSpec,
    maccormack: bool,
) -> VelocityField:
    positions = cell_centers(domain)
    flat_positions = positions.reshape(-1, 2)
    departure = rk2_backtrace(velocity, flat_positions, dt, domain)
    first = sample_vector(velocity, departure, domain).reshape(velocity.shape)
    if not maccormack:
        return first
    forward_points = rk2_backtrace(first, flat_positions, -dt, domain)
    forward = sample_vector(first, forward_points, domain).reshape(velocity.shape)
    corrected = first + 0.5 * (velocity - forward)
    lower, upper = local_velocity_bounds(velocity, domain)
    lower = np.minimum(lower, first)
    upper = np.maximum(upper, first)
    return np.asarray(np.clip(corrected, lower, upper), dtype=velocity.dtype)


def rk2_backtrace(
    velocity: VelocityField,
    points: Float[np.ndarray, "point 2"],
    dt: float,
    domain: DomainSpec,
) -> Float[np.ndarray, "point 2"]:
    """Trace points backward through a frozen velocity field with midpoint RK2."""
    initial_velocity = sample_vector(velocity, points, domain)
    midpoint = points - 0.5 * dt * initial_velocity
    midpoint_velocity = sample_vector(velocity, midpoint, domain)
    return points - dt * midpoint_velocity


def local_velocity_bounds(
    velocity: VelocityField,
    domain: DomainSpec,
) -> tuple[VelocityField, VelocityField]:
    """Return componentwise extrema over each cell's 3-by-3 neighborhood."""
    ny, nx, _ = velocity.shape
    rows = np.arange(ny, dtype=np.int64)
    columns = np.arange(nx, dtype=np.int64)
    lower = velocity.copy()
    upper = velocity.copy()
    for offset_y in (-1, 0, 1):
        selected_rows = rows + offset_y
        selected_rows = np.asarray(
            np.mod(selected_rows, ny)
            if "y" in domain.periodic_axes
            else np.clip(selected_rows, 0, ny - 1),
            dtype=np.int64,
        )
        for offset_x in (-1, 0, 1):
            selected_columns = columns + offset_x
            selected_columns = np.asarray(
                np.mod(selected_columns, nx)
                if "x" in domain.periodic_axes
                else np.clip(selected_columns, 0, nx - 1),
                dtype=np.int64,
            )
            neighbor = velocity[selected_rows[:, None], selected_columns[None, :], :]
            lower = np.minimum(lower, neighbor)
            upper = np.maximum(upper, neighbor)
    return lower, upper


def wall_velocity_grid(
    domain: DomainSpec,
    wall_velocity_at: Callable[[Float[np.ndarray, "point 2"]], Float[np.ndarray, "point 2"]],
) -> VelocityField:
    points = cell_centers(domain).reshape(-1, 2)
    return wall_velocity_at(points).reshape(domain.ny, domain.nx, 2)
