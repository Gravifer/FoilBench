import numpy as np

from foilbench_py.core.grid import (
    apply_domain_boundaries,
    cell_to_faces,
    enforce_solid_faces,
    project_faces,
)
from foilbench_py.core.models import DomainSpec
from foilbench_py.types import FaceVelocityX, FaceVelocityY, ScalarField


def _divergence(
    u: FaceVelocityX,
    v: FaceVelocityY,
    domain: DomainSpec,
) -> ScalarField:
    return (u[:, 1:] - u[:, :-1]) / domain.dx + (v[1:, :] - v[:-1, :]) / domain.dy


def test_preconditioned_masked_projection_reduces_fluid_divergence() -> None:
    domain = DomainSpec(2, ((-2.0, 2.0), (-1.0, 1.0)), (64, 32))
    rng = np.random.default_rng(7)
    velocity = rng.normal(0.0, 0.1, (32, 64, 2))
    velocity[:, :, 0] += 1.0
    solid = np.zeros((32, 64), dtype=np.bool_)
    solid[12:20, 28:36] = True
    wall = np.zeros_like(velocity)
    u, v = cell_to_faces(velocity)
    apply_domain_boundaries(u, v, domain, (1.0, 0.0))
    enforce_solid_faces(u, v, solid, wall)
    before = _divergence(u, v, domain)

    projected_u, projected_v, info = project_faces(
        u,
        v,
        domain,
        solid,
        wall,
        (1.0, 0.0),
        0.01,
    )
    after = _divergence(projected_u, projected_v, domain)

    assert info == 0
    assert np.linalg.norm(after[~solid]) < 0.25 * np.linalg.norm(before[~solid])
