# pyright: reportPrivateImportUsage=false
"""Shared diagnostics, with named-axis operations where they improve clarity."""

from dataclasses import dataclass

import einx
import numpy as np
from jaxtyping import Float

from foilbench_py.core.models import DomainSpec
from foilbench_py.types import MaskField, VelocityField


@dataclass(frozen=True, slots=True)
class WakeSpectrum:
    sample_count: int
    frequency_resolution: float
    transverse_rms: float
    dominant_frequency: float
    strouhal_number: float
    dominant_power_fraction: float


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
    chord: float = 1.0,
    freestream_u: float = 1.0,
    solid: MaskField | None = None,
    threshold: float = 0.1,
) -> float:
    centers_x = np.linspace(
        domain.bounds[0][0] + 0.5 * domain.dx,
        domain.bounds[0][1] - 0.5 * domain.dx,
        domain.nx,
    )
    wake_columns = centers_x > pivot_x + chord
    if not np.any(wake_columns):
        return 0.0
    active = freestream_u - velocity[:, wake_columns, 0] > (
        threshold * abs(freestream_u)
    )
    if solid is not None:
        active &= ~solid[:, wake_columns]
    active_rows = np.any(active, axis=1)
    return float(np.count_nonzero(active_rows) * domain.dy)


def recirculation_area(
    velocity: VelocityField,
    domain: DomainSpec,
    pivot_x: float,
    solid: MaskField | None = None,
) -> float:
    centers_x = np.linspace(
        domain.bounds[0][0] + 0.5 * domain.dx,
        domain.bounds[0][1] - 0.5 * domain.dx,
        domain.nx,
    )
    downstream = centers_x > pivot_x
    recirculating = velocity[:, downstream, 0] < 0.0
    if solid is not None:
        recirculating &= ~solid[:, downstream]
    return float(np.count_nonzero(recirculating) * domain.dx * domain.dy)


def analyze_wake_probe(
    transverse_velocity: Float[np.ndarray, " sample"],
    sample_dt: float,
    chord: float,
    freestream_speed: float,
) -> WakeSpectrum:
    """Describe periodic content in a uniformly sampled transverse wake probe."""
    if transverse_velocity.ndim != 1:
        raise ValueError("wake probe samples must be one-dimensional")
    if transverse_velocity.size < 8:
        raise ValueError("wake spectrum requires at least eight samples")
    if sample_dt <= 0.0 or chord <= 0.0 or freestream_speed <= 0.0:
        raise ValueError("sample_dt, chord, and freestream_speed must be positive")
    if not np.isfinite(transverse_velocity).all():
        raise ValueError("wake probe samples must be finite")

    centered = transverse_velocity - np.mean(transverse_velocity)
    transverse_rms = float(np.sqrt(np.mean(centered * centered)))
    windowed = centered * np.hanning(centered.size)
    power = np.abs(np.fft.rfft(windowed)) ** 2
    power[0] = 0.0
    total_power = float(np.sum(power))
    frequency_resolution = 1.0 / (centered.size * sample_dt)
    if total_power <= np.finfo(np.float64).tiny:
        return WakeSpectrum(
            transverse_velocity.size,
            frequency_resolution,
            transverse_rms,
            0.0,
            0.0,
            0.0,
        )

    dominant_index = int(np.argmax(power))
    frequency = float(np.fft.rfftfreq(centered.size, sample_dt)[dominant_index])
    return WakeSpectrum(
        transverse_velocity.size,
        frequency_resolution,
        transverse_rms,
        frequency,
        frequency * chord / freestream_speed,
        float(power[dominant_index] / total_power),
    )
