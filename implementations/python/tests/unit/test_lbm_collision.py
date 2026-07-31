# pyright: reportPrivateUsage=false

import numpy as np
import pytest

from foilbench_py.solvers._numba_adapter import lbm_trt_collision

_OPPOSITE = np.asarray([0, 3, 4, 1, 2, 7, 8, 5, 6], dtype=np.int64)
_C = np.asarray(
    [[0, 0], [1, 0], [0, 1], [-1, 0], [0, -1], [1, 1], [-1, 1], [-1, -1], [1, -1]],
    dtype=np.float64,
)
_W = np.asarray([4 / 9, 1 / 9, 1 / 9, 1 / 9, 1 / 9, 1 / 36, 1 / 36, 1 / 36, 1 / 36])


@pytest.mark.parametrize("dtype", [np.float32, np.float64])
def test_compiled_trt_collision_matches_numpy_reference(
    dtype: type[np.float32] | type[np.float64],
) -> None:
    rng = np.random.default_rng(17)
    populations = np.asarray(0.05 + rng.random((5, 7, 9)), dtype=dtype)
    omega_plus = 1.7
    omega_minus = 0.4
    density = np.sum(populations, axis=2)
    momentum = np.einsum("yxd,dc->yxc", populations, _C).astype(dtype)
    velocity = momentum / density[:, :, None]
    projection = np.einsum("dc,yxc->yxd", _C, velocity)
    speed_squared = np.sum(velocity * velocity, axis=2)
    equilibrium = density[:, :, None] * _W[None, None, :] * (
        1.0 + 3.0 * projection + 4.5 * projection * projection
        - 1.5 * speed_squared[:, :, None]
    )
    delta = populations - equilibrium
    expected = populations - 0.5 * (omega_plus + omega_minus) * delta - 0.5 * (
        omega_plus - omega_minus
    ) * delta[:, :, _OPPOSITE]

    actual_density, actual = lbm_trt_collision(populations, omega_plus, omega_minus)

    np.testing.assert_allclose(actual_density, density, rtol=2.0e-6)
    np.testing.assert_allclose(actual, expected, rtol=2.0e-5, atol=2.0e-6)
