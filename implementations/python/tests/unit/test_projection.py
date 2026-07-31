import numpy as np
import pytest

from foilbench_py.core._scipy_adapter import solve_masked_poisson
from foilbench_py.core.grid import (
    advect_faces,
    advect_faces_skew_rk2,
    advect_velocity,
    apply_domain_boundaries,
    cell_to_faces,
    enforce_solid_faces,
    local_velocity_bounds,
    project_faces,
    rk2_backtrace,
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


def test_masked_projection_rejects_non_finite_rhs_before_cg() -> None:
    rhs = np.zeros((8, 8), dtype=np.float32)
    rhs[3, 4] = np.inf
    fluid = np.ones_like(rhs, dtype=np.bool_)

    with pytest.raises(FloatingPointError, match="pressure RHS"):
        solve_masked_poisson(rhs, fluid, 0.125, 0.125)


def test_rk2_backtrace_matches_midpoint_step_for_linear_rotation() -> None:
    domain = DomainSpec(2, ((-2.0, 2.0), (-2.0, 2.0)), (64, 64))
    x = np.linspace(-2.0 + 0.5 * domain.dx, 2.0 - 0.5 * domain.dx, domain.nx)
    y = np.linspace(-2.0 + 0.5 * domain.dy, 2.0 - 0.5 * domain.dy, domain.ny)
    xx, yy = np.meshgrid(x, y)
    velocity = np.stack((-yy, xx), axis=2)
    points = np.asarray([[1.0, 0.0], [0.0, 1.0]], dtype=np.float64)

    departure = rk2_backtrace(velocity, points, 0.1, domain)

    np.testing.assert_allclose(
        departure,
        np.asarray([[0.995, -0.1], [0.1, 0.995]]),
        atol=1.0e-12,
    )


def test_limited_maccormack_stays_inside_local_component_bounds() -> None:
    domain = DomainSpec(2, ((0.0, 1.0), (0.0, 1.0)), (32, 32), ("x", "y"))
    velocity = np.zeros((domain.ny, domain.nx, 2), dtype=np.float64)
    velocity[:, : domain.nx // 2, 0] = 0.8
    velocity[domain.ny // 3 : 2 * domain.ny // 3, :, 1] = -0.4
    lower, upper = local_velocity_bounds(velocity, domain)

    advected = advect_velocity(velocity, 0.02, domain, maccormack=True)

    assert np.all(advected >= lower - 1.0e-12)
    assert np.all(advected <= upper + 1.0e-12)


def test_face_advection_preserves_constant_mac_velocity() -> None:
    domain = DomainSpec(2, ((-2.0, 2.0), (-1.0, 1.0)), (32, 16))
    u = np.full((domain.ny, domain.nx + 1), 1.25, dtype=np.float64)
    v = np.full((domain.ny + 1, domain.nx), -0.2, dtype=np.float64)

    advected_u, advected_v = advect_faces(u, v, 0.03, domain, maccormack=True)

    np.testing.assert_allclose(advected_u, u)
    np.testing.assert_allclose(advected_v, v)


def test_skew_rk2_advection_preserves_constant_mac_velocity() -> None:
    domain = DomainSpec(2, ((-2.0, 2.0), (-1.0, 1.0)), (32, 16))
    u = np.full((domain.ny, domain.nx + 1), 1.25, dtype=np.float64)
    v = np.full((domain.ny + 1, domain.nx), -0.2, dtype=np.float64)
    solid = np.zeros((domain.ny, domain.nx), dtype=np.bool_)
    wall = np.zeros((domain.ny, domain.nx, 2), dtype=np.float64)

    advected_u, advected_v = advect_faces_skew_rk2(u, v, 0.03, domain, solid, wall, (1.25, -0.2))

    np.testing.assert_allclose(advected_u, u)
    np.testing.assert_allclose(advected_v, v)
