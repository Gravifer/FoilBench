# pyright: reportMissingTypeStubs=false
# pyright: reportUnknownArgumentType=false
# pyright: reportUnknownMemberType=false
# pyright: reportUnknownVariableType=false
"""Typed boundary around kernels excluded from Jaxtyping's import hook."""

from foilbench_kernels.pic import particle_to_grid_kernel
from foilbench_py.types import ParticleVelocity, PointCloud, VelocityField


def particle_to_grid(
    positions: PointCloud,
    velocities: ParticleVelocity,
    x0: float,
    y0: float,
    dx: float,
    dy: float,
    nx: int,
    ny: int,
    freestream: tuple[float, ...],
) -> VelocityField:
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
