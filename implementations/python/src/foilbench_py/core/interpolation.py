"""Typed bilinear sampling utilities."""

import numpy as np
from jaxtyping import Float

from foilbench_py.core.models import DomainSpec


def sample_scalar(
    field: Float[np.ndarray, "ny nx"],
    points: Float[np.ndarray, "point 2"],
    domain: DomainSpec,
) -> Float[np.ndarray, " point"]:
    if field.shape != (domain.ny, domain.nx):
        raise ValueError(
            f"scalar field shape {field.shape} does not match {(domain.ny, domain.nx)}"
        )
    if points.ndim != 2 or points.shape[1] != domain.dimension:
        raise ValueError("sample points must match the domain dimension")
    if domain.dimension != 2:
        raise NotImplementedError("Phase 1 interpolation supports only 2D")
    x0, _ = domain.bounds[0]
    y0, _ = domain.bounds[1]
    gx = (points[:, 0] - x0) / domain.dx - 0.5
    gy = (points[:, 1] - y0) / domain.dy - 0.5
    ix0 = np.floor(gx).astype(np.int64)
    iy0 = np.floor(gy).astype(np.int64)
    tx = gx - ix0
    ty = gy - iy0
    ix0 = np.clip(ix0, 0, domain.nx - 1)
    iy0 = np.clip(iy0, 0, domain.ny - 1)
    ix1 = np.clip(ix0 + 1, 0, domain.nx - 1)
    iy1 = np.clip(iy0 + 1, 0, domain.ny - 1)
    sampled = (
        (1.0 - tx) * (1.0 - ty) * field[iy0, ix0]
        + tx * (1.0 - ty) * field[iy0, ix1]
        + (1.0 - tx) * ty * field[iy1, ix0]
        + tx * ty * field[iy1, ix1]
    )
    return np.asarray(sampled, dtype=field.dtype)


def sample_vector(
    field: Float[np.ndarray, "ny nx dim"],
    points: Float[np.ndarray, "point 2"],
    domain: DomainSpec,
) -> Float[np.ndarray, "point dim"]:
    if field.shape != (domain.ny, domain.nx, domain.dimension):
        raise ValueError(
            "vector field shape does not match the domain resolution and dimension"
        )
    if points.ndim != 2 or points.shape[1] != domain.dimension:
        raise ValueError("sample points must match the domain dimension")
    components = [
        sample_scalar(field[:, :, component], points, domain) for component in range(field.shape[2])
    ]
    return np.stack(components, axis=1)
