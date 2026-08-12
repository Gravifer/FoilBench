import numpy as np
import pytest

from foilbench_py.core._scipy_adapter import solve_masked_poisson
from foilbench_py.core.grid import (
    _derivative,
    advect_faces,
    advect_faces_skew_rk2,
    advect_velocity,
    apply_domain_boundaries,
    cell_to_faces,
    enforce_solid_faces,
    faces_to_cell,
    implicit_diffuse_faces,
    local_velocity_bounds,
    project_faces,
    rk2_backtrace,
    skew_face_advection_rate,
)
from foilbench_py.core.models import DomainSpec, NumericalFailure
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

    projected_u, projected_v, report = project_faces(
        u,
        v,
        domain,
        solid,
        wall,
        (1.0, 0.0),
        0.01,
    )
    after = _divergence(projected_u, projected_v, domain)

    assert report.converged
    assert report.final_residual <= report.tolerance * (1.0 + 1.0e-6)
    assert np.linalg.norm(after[~solid]) < 0.25 * np.linalg.norm(before[~solid])


def test_periodic_projection_updates_wrapped_normal_faces() -> None:
    domain = DomainSpec(2, ((0.0, 2.0), (0.0, 1.0)), (16, 12), ("x", "y"))
    rng = np.random.default_rng(19)
    u = rng.normal(0.0, 0.1, (domain.ny, domain.nx + 1))
    v = rng.normal(0.0, 0.1, (domain.ny + 1, domain.nx))
    solid = np.zeros((domain.ny, domain.nx), dtype=np.bool_)
    wall = np.zeros((domain.ny, domain.nx, 2), dtype=np.float64)
    apply_domain_boundaries(u, v, domain, (0.0, 0.0))
    before = _divergence(u, v, domain)

    projected_u, projected_v, report = project_faces(
        u,
        v,
        domain,
        solid,
        wall,
        (0.0, 0.0),
        0.01,
        pressure_tolerance=1.0e-9,
    )
    after = _divergence(projected_u, projected_v, domain)

    assert report.converged
    assert report.final_residual <= report.tolerance * (1.0 + 1.0e-6)
    assert np.linalg.norm(after) < 1.0e-5 * np.linalg.norm(before)
    np.testing.assert_allclose(projected_u[:, 0], projected_u[:, -1])
    np.testing.assert_allclose(projected_v[0, :], projected_v[-1, :])


def test_periodic_face_diffusion_matches_discrete_fourier_decay() -> None:
    domain = DomainSpec(2, ((0.0, 2.0), (0.0, 1.0)), (32, 16), ("x", "y"))
    phase = 2.0 * np.pi * np.arange(domain.nx + 1) / domain.nx
    u = np.broadcast_to(np.sin(phase), (domain.ny, domain.nx + 1)).copy()
    v = np.zeros((domain.ny + 1, domain.nx), dtype=np.float64)
    viscosity = 0.2
    dt = 0.03
    original = u.copy()

    diffused_u, _, report = implicit_diffuse_faces(
        u,
        v,
        viscosity,
        dt,
        domain,
        tolerance=1.0e-10,
    )

    eigenvalue = 4.0 * np.sin(np.pi / domain.nx) ** 2 / domain.dx**2
    factor = 1.0 / (1.0 + viscosity * dt * eigenvalue)
    assert report.converged
    np.testing.assert_allclose(diffused_u[:, :-1], factor * original[:, :-1], atol=1.0e-8)
    np.testing.assert_allclose(diffused_u[:, 0], diffused_u[:, -1], atol=1.0e-12)


def test_periodic_face_derivative_uses_unique_logical_endpoints() -> None:
    domain = DomainSpec(2, ((0.0, 2.0), (0.0, 1.0)), (32, 16), ("x", "y"))
    phase_x = 2.0 * np.pi * np.arange(domain.nx + 1) / domain.nx
    u = np.broadcast_to(np.sin(phase_x), (domain.ny, domain.nx + 1)).copy()
    derivative_u = _derivative(
        u, domain.dx, 1, True, duplicate_endpoint=True
    )
    expected_x = np.sin(2.0 * np.pi / domain.nx) / domain.dx
    np.testing.assert_allclose(derivative_u[:, 0], expected_x, atol=1.0e-12)
    np.testing.assert_allclose(derivative_u[:, -1], derivative_u[:, 0], atol=1.0e-12)

    phase_y = 2.0 * np.pi * np.arange(domain.ny + 1) / domain.ny
    v = np.broadcast_to(np.sin(phase_y)[:, None], (domain.ny + 1, domain.nx)).copy()
    derivative_v = _derivative(
        v, domain.dy, 0, True, duplicate_endpoint=True
    )
    expected_y = np.sin(2.0 * np.pi / domain.ny) / domain.dy
    np.testing.assert_allclose(derivative_v[0, :], expected_y, atol=1.0e-12)
    np.testing.assert_allclose(derivative_v[-1, :], derivative_v[0, :], atol=1.0e-12)


def test_masked_projection_rejects_non_finite_rhs_before_cg() -> None:
    rhs = np.zeros((8, 8), dtype=np.float32)
    rhs[3, 4] = np.inf
    fluid = np.ones_like(rhs, dtype=np.bool_)

    with pytest.raises(NumericalFailure, match="pressure RHS") as captured:
        solve_masked_poisson(rhs, fluid, 0.125, 0.125)
    assert captured.value.reason == "nonfinite_state"


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


def test_skew_cfl_uses_native_faces_when_cell_averages_cancel() -> None:
    domain = DomainSpec(2, ((0.0, 2.0), (-1.0, 1.0)), (4, 4))
    u = np.empty((4, 5), dtype=np.float32)
    u[:, 0::2] = 2.0
    u[:, 1::2] = -2.0
    v = np.zeros((5, 4), dtype=np.float32)

    np.testing.assert_allclose(faces_to_cell(u, v), 0.0)
    assert skew_face_advection_rate(u, v, domain) == pytest.approx(4.0)


def test_skew_rk2_advection_preserves_constant_mac_velocity() -> None:
    domain = DomainSpec(2, ((-2.0, 2.0), (-1.0, 1.0)), (32, 16))
    u = np.full((domain.ny, domain.nx + 1), 1.25, dtype=np.float64)
    v = np.full((domain.ny + 1, domain.nx), -0.2, dtype=np.float64)
    solid = np.zeros((domain.ny, domain.nx), dtype=np.bool_)
    wall = np.zeros((domain.ny, domain.nx, 2), dtype=np.float64)

    advected_u, advected_v = advect_faces_skew_rk2(u, v, 0.03, domain, solid, wall, (1.25, -0.2))

    np.testing.assert_allclose(advected_u, u)
    np.testing.assert_allclose(advected_v, v)
