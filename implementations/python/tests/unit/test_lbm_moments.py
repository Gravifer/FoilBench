# pyright: reportPrivateUsage=false

import numpy as np
import pytest

from foilbench_py.solvers.lbm import LBMSolver


@pytest.mark.parametrize("shape", [(3, 5), (7, 4)])
def test_einx_lbm_equilibrium_and_moments_match_numpy(
    shape: tuple[int, int],
) -> None:
    ny, nx = shape
    rng = np.random.default_rng(ny * 100 + nx)
    density = np.asarray(0.9 + 0.2 * rng.random(shape), dtype=np.float64)
    velocity = np.asarray(0.03 * rng.normal(size=(ny, nx, 2)), dtype=np.float64)
    solver = LBMSolver()
    c = solver._C.astype(np.float64)
    weights = solver._W.astype(np.float64)
    projection = np.einsum("dc,yxc->yxd", c, velocity)
    speed_squared = np.sum(velocity * velocity, axis=2)
    expected = density[:, :, None] * weights[None, None, :] * (
        1.0 + 3.0 * projection + 4.5 * projection * projection
        - 1.5 * speed_squared[:, :, None]
    )

    equilibrium = solver._equilibrium(density, velocity)
    recovered_density, recovered_velocity = solver._macroscopic(equilibrium)

    np.testing.assert_allclose(equilibrium, expected, rtol=1.0e-13, atol=1.0e-13)
    np.testing.assert_allclose(recovered_density, density, rtol=1.0e-13)
    np.testing.assert_allclose(recovered_velocity, velocity, rtol=1.0e-13, atol=1.0e-13)
