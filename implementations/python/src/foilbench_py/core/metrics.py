# pyright: reportPrivateImportUsage=false
"""Shared diagnostics, with named-axis operations where they improve clarity."""

import einx
import numpy as np
from jaxtyping import Float

from foilbench_py.core.models import DomainSpec
from foilbench_py.types import MaskField, VelocityField


def speed_squared(
    velocity: VelocityField,
) -> Float[np.ndarray, "ny nx"]:
    return einx.sum("ny nx [component] -> ny nx", velocity * velocity)


def kinetic_energy(velocity: VelocityField) -> float:
    return 0.5 * float(np.mean(speed_squared(velocity)))


def momentum(
    velocity: VelocityField,
) -> Float[np.ndarray, " component"]:
    return einx.mean("[ny nx] component -> component", velocity)


def vorticity(velocity: VelocityField, domain: DomainSpec) -> Float[np.ndarray, "ny nx"]:
    du_dy = np.gradient(velocity[:, :, 0], domain.dy, axis=0)
    dv_dx = np.gradient(velocity[:, :, 1], domain.dx, axis=1)
    return dv_dx - du_dy


def enstrophy(velocity: VelocityField, domain: DomainSpec) -> float:
    omega = vorticity(velocity, domain)
    return 0.5 * float(np.mean(omega * omega))


def divergence_l2(velocity: VelocityField, domain: DomainSpec) -> float:
    du_dx = np.gradient(velocity[:, :, 0], domain.dx, axis=1)
    dv_dy = np.gradient(velocity[:, :, 1], domain.dy, axis=0)
    divergence = du_dx + dv_dy
    return float(np.sqrt(np.mean(divergence * divergence)))


def solid_leakage(velocity: VelocityField, solid: MaskField) -> float:
    if not np.any(solid):
        return 0.0
    return float(np.sqrt(np.max(speed_squared(velocity)[solid])))


def wake_width(
    velocity: VelocityField,
    domain: DomainSpec,
    pivot_x: float,
    threshold: float = 0.1,
) -> float:
    centers_x = np.linspace(
        domain.bounds[0][0] + 0.5 * domain.dx,
        domain.bounds[0][1] - 0.5 * domain.dx,
        domain.nx,
    )
    wake_columns = centers_x > pivot_x + 1.0
    if not np.any(wake_columns):
        return 0.0
    deficit = np.maximum(0.0, 1.0 - velocity[:, wake_columns, 0])
    active_rows = np.any(deficit > threshold, axis=1)
    return float(np.count_nonzero(active_rows) * domain.dy)


def recirculation_area(velocity: VelocityField, domain: DomainSpec, pivot_x: float) -> float:
    centers_x = np.linspace(
        domain.bounds[0][0] + 0.5 * domain.dx,
        domain.bounds[0][1] - 0.5 * domain.dx,
        domain.nx,
    )
    downstream = centers_x > pivot_x
    return float(np.count_nonzero(velocity[:, downstream, 0] < 0.0) * domain.dx * domain.dy)
