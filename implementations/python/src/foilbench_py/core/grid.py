"""Shared 2D MAC-grid operations."""

from collections.abc import Callable
from dataclasses import dataclass

import numpy as np
from jaxtyping import Float

from foilbench_py.core._scipy_adapter import solve_masked_poisson
from foilbench_py.core.geometry import cell_centers
from foilbench_py.core.interpolation import sample_staggered_scalar, sample_vector
from foilbench_py.core.models import DomainSpec
from foilbench_py.types import FaceVelocityX, FaceVelocityY, MaskField, VelocityField


@dataclass(frozen=True, slots=True)
class IterativeReport:
    criterion: str
    tolerance: float
    iterations: int
    final_residual: float
    converged: bool


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
        periodic_u = 0.5 * (u[:, 0] + u[:, -1])
        u[:, 0] = periodic_u
        u[:, -1] = periodic_u
    else:
        u[:, 0] = ux
        u[:, -1] = u[:, -2]
        v[:, 0] = uy
        v[:, -1] = v[:, -2]
    if "y" in domain.periodic_axes:
        periodic_v = 0.5 * (v[0, :] + v[-1, :])
        v[0, :] = periodic_v
        v[-1, :] = periodic_v
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


def native_divergence_linf(
    u: FaceVelocityX,
    v: FaceVelocityY,
    domain: DomainSpec,
    solid: MaskField,
) -> float:
    """Return the maximum native MAC divergence over fluid cells."""
    divergence = (u[:, 1:] - u[:, :-1]) / domain.dx + (
        v[1:, :] - v[:-1, :]
    ) / domain.dy
    fluid = ~solid
    return float(np.max(np.abs(divergence[fluid]))) if np.any(fluid) else 0.0


def solid_face_leakage(
    u: FaceVelocityX,
    v: FaceVelocityY,
    solid: MaskField,
    wall_velocity: VelocityField,
) -> float:
    """Return maximum wall-relative normal speed on fluid-solid MAC faces."""
    wall_u, wall_v = cell_to_faces(wall_velocity)
    interface_u = np.zeros_like(u, dtype=np.bool_)
    interface_v = np.zeros_like(v, dtype=np.bool_)
    interface_u[:, 1:-1] = solid[:, :-1] ^ solid[:, 1:]
    interface_v[1:-1, :] = solid[:-1, :] ^ solid[1:, :]
    maximum = 0.0
    if np.any(interface_u):
        maximum = max(maximum, float(np.max(np.abs(u[interface_u] - wall_u[interface_u]))))
    if np.any(interface_v):
        maximum = max(maximum, float(np.max(np.abs(v[interface_v] - wall_v[interface_v]))))
    return maximum


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
    pressure_max_iterations: int = 640,
) -> tuple[FaceVelocityX, FaceVelocityY, IterativeReport]:
    apply_domain_boundaries(u, v, domain, freestream, channel_walls)
    enforce_solid_faces(u, v, solid, wall_velocity)
    divergence = (u[:, 1:] - u[:, :-1]) / domain.dx + (v[1:, :] - v[:-1, :]) / domain.dy
    fluid = ~solid
    rhs = np.where(fluid, -divergence / max(dt, 1.0e-12), 0.0)
    # Keep interactive fields in their requested precision, but solve pressure
    # in Float64. SciPy's Float32 CG may declare convergence from its recursive
    # residual while the independently recomputed contract residual remains
    # above tolerance in strongly separated flows.
    pressure, info, iterations, relative_residual = solve_masked_poisson(
        np.asarray(rhs, dtype=np.float64),
        fluid,
        domain.dx,
        domain.dy,
        domain.periodic_axes,
        pressure_tolerance,
        pressure_max_iterations,
    )
    u[:, 1:-1] -= dt * (pressure[:, 1:] - pressure[:, :-1]) / domain.dx
    v[1:-1, :] -= dt * (pressure[1:, :] - pressure[:-1, :]) / domain.dy
    if "x" in domain.periodic_axes:
        u[:, 0] -= dt * (pressure[:, 0] - pressure[:, -1]) / domain.dx
        u[:, -1] = u[:, 0]
    if "y" in domain.periodic_axes:
        v[0, :] -= dt * (pressure[0, :] - pressure[-1, :]) / domain.dy
        v[-1, :] = v[0, :]
    apply_domain_boundaries(u, v, domain, freestream, channel_walls)
    enforce_solid_faces(u, v, solid, wall_velocity)
    return u, v, IterativeReport(
        "relative-l2",
        pressure_tolerance,
        iterations,
        relative_residual,
        info == 0 and relative_residual <= pressure_tolerance * (1.0 + 1.0e-6),
    )


def implicit_diffuse(
    velocity: VelocityField,
    viscosity: float,
    dt: float,
    domain: DomainSpec,
    tolerance: float = 1.0e-5,
    max_iterations: int = 640,
) -> tuple[VelocityField, IterativeReport]:
    components: list[Float[np.ndarray, "ny nx"]] = []
    performed = 0
    residual = 0.0
    converged = True
    for component in range(velocity.shape[2]):
        selected, iterations, final_residual, component_converged = (
            _implicit_diffuse_scalar(
                velocity[:, :, component],
                viscosity,
                dt,
                domain,
                tolerance,
                max_iterations,
            )
        )
        components.append(selected)
        performed = max(performed, iterations)
        residual = max(residual, final_residual)
        converged = converged and component_converged
    return np.stack(components, axis=2), IterativeReport(
        "update-linf",
        tolerance,
        performed,
        residual,
        converged,
    )


def _implicit_diffuse_scalar(
    field: Float[np.ndarray, "field_y field_x"],
    viscosity: float,
    dt: float,
    domain: DomainSpec,
    tolerance: float = 1.0e-5,
    max_iterations: int = 640,
) -> tuple[Float[np.ndarray, "field_y field_x"], int, float, bool]:
    if viscosity <= 0.0:
        return field.copy(), 0, 0.0, True
    periodic_x = "x" in domain.periodic_axes
    periodic_y = "y" in domain.periodic_axes
    duplicate_x = periodic_x and field.shape[1] == domain.nx + 1
    duplicate_y = periodic_y and field.shape[0] == domain.ny + 1
    logical = field[
        : field.shape[0] - int(duplicate_y),
        : field.shape[1] - int(duplicate_x),
    ]
    ax = viscosity * dt / (domain.dx * domain.dx)
    ay = viscosity * dt / (domain.dy * domain.dy)
    denominator = 1.0 + 2.0 * ax + 2.0 * ay
    original = logical.copy()
    result = logical.copy()
    scale = max(float(np.max(np.abs(original))), 1.0)
    final_residual = float("inf")
    performed = 0
    for iteration in range(1, max_iterations + 1):
        performed = iteration
        left = np.roll(result, 1, axis=1) if periodic_x else np.concatenate(
            (result[:, :1], result[:, :-1]), axis=1
        )
        right = np.roll(result, -1, axis=1) if periodic_x else np.concatenate(
            (result[:, 1:], result[:, -1:]), axis=1
        )
        lower = np.roll(result, 1, axis=0) if periodic_y else np.concatenate(
            (result[:1, :], result[:-1, :]), axis=0
        )
        upper = np.roll(result, -1, axis=0) if periodic_y else np.concatenate(
            (result[1:, :], result[-1:, :]), axis=0
        )
        updated = (
            original
            + ax * (left + right)
            + ay * (lower + upper)
        ) / denominator
        final_residual = float(np.max(np.abs(updated - result))) / scale
        result = updated
        if final_residual <= tolerance:
            break
    converged = final_residual <= tolerance
    output = field.copy()
    output[: result.shape[0], : result.shape[1]] = result
    if duplicate_x:
        output[:, -1] = output[:, 0]
    if duplicate_y:
        output[-1, :] = output[0, :]
    return np.asarray(output, dtype=field.dtype), performed, final_residual, converged


def implicit_diffuse_faces(
    u: FaceVelocityX,
    v: FaceVelocityY,
    viscosity: float,
    dt: float,
    domain: DomainSpec,
    tolerance: float = 1.0e-5,
    max_iterations: int = 640,
) -> tuple[FaceVelocityX, FaceVelocityY, IterativeReport]:
    selected_u, u_iterations, u_residual, u_converged = _implicit_diffuse_scalar(
        u, viscosity, dt, domain, tolerance, max_iterations
    )
    selected_v, v_iterations, v_residual, v_converged = _implicit_diffuse_scalar(
        v, viscosity, dt, domain, tolerance, max_iterations
    )
    return (
        selected_u,
        selected_v,
        IterativeReport(
            "update-linf",
            tolerance,
            max(u_iterations, v_iterations),
            max(u_residual, v_residual),
            u_converged and v_converged,
        ),
    )


def _face_positions(
    domain: DomainSpec,
) -> tuple[Float[np.ndarray, "u_point 2"], Float[np.ndarray, "v_point 2"]]:
    x0, x1 = domain.bounds[0]
    y0, y1 = domain.bounds[1]
    u_x = np.linspace(x0, x1, domain.nx + 1)
    u_y = np.linspace(y0 + 0.5 * domain.dy, y1 - 0.5 * domain.dy, domain.ny)
    v_x = np.linspace(x0 + 0.5 * domain.dx, x1 - 0.5 * domain.dx, domain.nx)
    v_y = np.linspace(y0, y1, domain.ny + 1)
    u_xx, u_yy = np.meshgrid(u_x, u_y)
    v_xx, v_yy = np.meshgrid(v_x, v_y)
    return (
        np.stack((u_xx, u_yy), axis=2).reshape(-1, 2),
        np.stack((v_xx, v_yy), axis=2).reshape(-1, 2),
    )


def _advect_face_component(
    field: Float[np.ndarray, "field_y field_x"],
    velocity: VelocityField,
    points: Float[np.ndarray, "point 2"],
    offset: tuple[float, float],
    dt: float,
    domain: DomainSpec,
) -> Float[np.ndarray, "field_y field_x"]:
    departure = rk2_backtrace(velocity, points, dt, domain)
    return sample_staggered_scalar(field, departure, domain, offset).reshape(field.shape)


def _face_local_bounds(
    field: Float[np.ndarray, "field_y field_x"],
) -> tuple[Float[np.ndarray, "field_y field_x"], Float[np.ndarray, "field_y field_x"]]:
    padded = np.pad(field, 1, mode="edge")
    neighbors = [
        padded[
            1 + offset_y : 1 + offset_y + field.shape[0],
            1 + offset_x : 1 + offset_x + field.shape[1],
        ]
        for offset_y in (-1, 0, 1)
        for offset_x in (-1, 0, 1)
    ]
    return np.minimum.reduce(neighbors), np.maximum.reduce(neighbors)


def advect_faces(
    u: FaceVelocityX,
    v: FaceVelocityY,
    dt: float,
    domain: DomainSpec,
    maccormack: bool,
) -> tuple[FaceVelocityX, FaceVelocityY]:
    """Advect MAC components without a dissipative cell/face round trip."""
    velocity = faces_to_cell(u, v)
    u_points, v_points = _face_positions(domain)
    first_u = _advect_face_component(u, velocity, u_points, (0.0, 0.5), dt, domain)
    first_v = _advect_face_component(v, velocity, v_points, (0.5, 0.0), dt, domain)
    if not maccormack:
        return first_u, first_v
    forward_velocity = faces_to_cell(first_u, first_v)
    forward_u = _advect_face_component(first_u, forward_velocity, u_points, (0.0, 0.5), -dt, domain)
    forward_v = _advect_face_component(first_v, forward_velocity, v_points, (0.5, 0.0), -dt, domain)
    corrected_u = first_u + 0.5 * (u - forward_u)
    corrected_v = first_v + 0.5 * (v - forward_v)
    lower_u, upper_u = _face_local_bounds(u)
    lower_v, upper_v = _face_local_bounds(v)
    corrected_u = np.clip(corrected_u, lower_u, upper_u)
    corrected_v = np.clip(corrected_v, lower_v, upper_v)
    return (
        np.asarray(corrected_u, dtype=u.dtype),
        np.asarray(corrected_v, dtype=v.dtype),
    )


def centered_derivative(
    field: Float[np.ndarray, "field_y field_x"],
    spacing: float,
    axis: int,
    periodic: bool,
    *,
    duplicate_endpoint: bool = False,
) -> Float[np.ndarray, "field_y field_x"]:
    if periodic:
        if duplicate_endpoint:
            logical_length = int(field.shape[axis]) - 1
            indices: np.ndarray[tuple[int], np.dtype[np.intp]] = np.arange(
                logical_length, dtype=np.intp
            )
            logical = np.take(field, indices, axis=axis)
            logical_derivative = (
                np.roll(logical, -1, axis=axis) - np.roll(logical, 1, axis=axis)
            ) / (2.0 * spacing)
            first = np.take(logical_derivative, [0], axis=axis)
            return np.asarray(
                np.concatenate((logical_derivative, first), axis=axis),
                dtype=field.dtype,
            )
        return np.asarray(
            (np.roll(field, -1, axis=axis) - np.roll(field, 1, axis=axis)) / (2.0 * spacing),
            dtype=field.dtype,
        )
    return np.asarray(np.gradient(field, spacing, axis=axis, edge_order=2), dtype=field.dtype)


def _cross_velocity_on_faces(
    u: FaceVelocityX,
    v: FaceVelocityY,
    domain: DomainSpec,
) -> tuple[FaceVelocityX, FaceVelocityY]:
    cell_u = 0.5 * (u[:, :-1] + u[:, 1:])
    cell_v = 0.5 * (v[:-1, :] + v[1:, :])
    v_on_u = np.empty_like(u)
    v_on_u[:, 1:-1] = 0.5 * (cell_v[:, :-1] + cell_v[:, 1:])
    if "x" in domain.periodic_axes:
        periodic_v = 0.5 * (cell_v[:, -1] + cell_v[:, 0])
        v_on_u[:, 0] = periodic_v
        v_on_u[:, -1] = periodic_v
    else:
        v_on_u[:, 0] = cell_v[:, 0]
        v_on_u[:, -1] = cell_v[:, -1]
    u_on_v = np.empty_like(v)
    u_on_v[1:-1, :] = 0.5 * (cell_u[:-1, :] + cell_u[1:, :])
    if "y" in domain.periodic_axes:
        periodic_u = 0.5 * (cell_u[-1, :] + cell_u[0, :])
        u_on_v[0, :] = periodic_u
        u_on_v[-1, :] = periodic_u
    else:
        u_on_v[0, :] = cell_u[0, :]
        u_on_v[-1, :] = cell_u[-1, :]
    return v_on_u, u_on_v


def skew_face_advection_rate(
    u: FaceVelocityX,
    v: FaceVelocityY,
    domain: DomainSpec,
) -> float:
    """Return the native staggered-grid rate used by the skew-RK2 CFL."""
    v_on_u, u_on_v = _cross_velocity_on_faces(u, v, domain)
    u_rate = np.abs(u) / domain.dx + np.abs(v_on_u) / domain.dy
    v_rate = np.abs(u_on_v) / domain.dx + np.abs(v) / domain.dy
    return max(float(np.max(u_rate)), float(np.max(v_rate)))


def _skew_symmetric_convection(
    u: FaceVelocityX,
    v: FaceVelocityY,
    domain: DomainSpec,
) -> tuple[FaceVelocityX, FaceVelocityY]:
    v_on_u, u_on_v = _cross_velocity_on_faces(u, v, domain)
    periodic_x = "x" in domain.periodic_axes
    periodic_y = "y" in domain.periodic_axes
    du_dx = centered_derivative(
        u, domain.dx, 1, periodic_x, duplicate_endpoint=periodic_x
    )
    du_dy = centered_derivative(u, domain.dy, 0, periodic_y)
    dv_dx = centered_derivative(v, domain.dx, 1, periodic_x)
    dv_dy = centered_derivative(
        v, domain.dy, 0, periodic_y, duplicate_endpoint=periodic_y
    )
    advective_u = u * du_dx + v_on_u * du_dy
    advective_v = u_on_v * dv_dx + v * dv_dy
    conservative_u = centered_derivative(
        u * u, domain.dx, 1, periodic_x, duplicate_endpoint=periodic_x
    ) + centered_derivative(
        v_on_u * u, domain.dy, 0, periodic_y
    )
    conservative_v = centered_derivative(
        u_on_v * v, domain.dx, 1, periodic_x
    ) + centered_derivative(
        v * v, domain.dy, 0, periodic_y, duplicate_endpoint=periodic_y
    )
    return (
        np.asarray(0.5 * (advective_u + conservative_u), dtype=u.dtype),
        np.asarray(0.5 * (advective_v + conservative_v), dtype=v.dtype),
    )


def advect_faces_skew_rk2(
    u: FaceVelocityX,
    v: FaceVelocityY,
    dt: float,
    domain: DomainSpec,
    solid: MaskField,
    wall_velocity: VelocityField,
    freestream: tuple[float, ...],
) -> tuple[FaceVelocityX, FaceVelocityY]:
    """Explicit midpoint step for skew-symmetric MAC convection."""
    first_u, first_v = _skew_symmetric_convection(u, v, domain)
    midpoint_u = np.asarray(u - 0.5 * dt * first_u, dtype=u.dtype)
    midpoint_v = np.asarray(v - 0.5 * dt * first_v, dtype=v.dtype)
    apply_domain_boundaries(midpoint_u, midpoint_v, domain, freestream)
    enforce_solid_faces(midpoint_u, midpoint_v, solid, wall_velocity)
    second_u, second_v = _skew_symmetric_convection(midpoint_u, midpoint_v, domain)
    advected_u = np.asarray(u - dt * second_u, dtype=u.dtype)
    advected_v = np.asarray(v - dt * second_v, dtype=v.dtype)
    apply_domain_boundaries(advected_u, advected_v, domain, freestream)
    enforce_solid_faces(advected_u, advected_v, solid, wall_velocity)
    return advected_u, advected_v


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
