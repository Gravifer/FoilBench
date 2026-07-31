import numpy as np
import pytest

from foilbench_py.solvers._numba_adapter import grid_to_particle


def _quadratic_weight(distance: float) -> float:
    absolute = abs(distance)
    if absolute < 0.5:
        return 0.75 - absolute * absolute
    if absolute < 1.5:
        difference = 1.5 - absolute
        return 0.5 * difference * difference
    return 0.0


def _reference_grid_to_particle(
    grid: np.ndarray,
    positions: np.ndarray,
    x0: float,
    y0: float,
    dx: float,
    dy: float,
) -> np.ndarray:
    ny, nx, _ = grid.shape
    result = np.empty_like(positions)
    for particle, position in enumerate(positions):
        gx = (position[0] - x0) / dx - 0.5
        gy = (position[1] - y0) / dy - 0.5
        base_x = int(np.floor(gx - 0.5))
        base_y = int(np.floor(gy - 0.5))
        value = np.zeros(2, dtype=np.float64)
        weight_sum = 0.0
        for offset_y in range(3):
            source_y = base_y + offset_y
            target_y = min(max(source_y, 0), ny - 1)
            wy = _quadratic_weight(gy - source_y)
            for offset_x in range(3):
                source_x = base_x + offset_x
                target_x = min(max(source_x, 0), nx - 1)
                weight = wy * _quadratic_weight(gx - source_x)
                value += weight * grid[target_y, target_x]
                weight_sum += weight
        result[particle] = value / weight_sum
    return result


@pytest.mark.parametrize(("shape", "dtype"), [((4, 5), np.float32), ((6, 8), np.float64)])
def test_quadratic_grid_to_particle_matches_numpy_reference(
    shape: tuple[int, int],
    dtype: type[np.float32] | type[np.float64],
) -> None:
    ny, nx = shape
    x0 = -1.25
    y0 = -0.75
    dx = 0.2
    dy = 0.15
    grid = np.arange(ny * nx * 2, dtype=dtype).reshape(ny, nx, 2) / 17.0
    positions = np.asarray(
        [
            [x0 + 0.01 * dx, y0 + 0.02 * dy],
            [x0 + 1.3 * dx, y0 + 2.7 * dy],
            [x0 + (nx - 0.01) * dx, y0 + (ny - 0.02) * dy],
        ],
        dtype=dtype,
    )

    actual = grid_to_particle(grid, positions, x0, y0, dx, dy)
    expected = _reference_grid_to_particle(grid, positions, x0, y0, dx, dy)

    assert actual.dtype == dtype
    np.testing.assert_allclose(actual, expected, rtol=2.0e-6, atol=2.0e-6)


@pytest.mark.parametrize("dtype", [np.float32, np.float64])
def test_quadratic_grid_to_particle_preserves_constant_velocity(
    dtype: type[np.float32] | type[np.float64],
) -> None:
    grid = np.empty((5, 7, 2), dtype=dtype)
    grid[...] = np.asarray([1.25, -0.375], dtype=dtype)
    positions = np.asarray(
        [[-0.99, -0.49], [-0.25, 0.0], [0.99, 0.49]],
        dtype=dtype,
    )

    actual = grid_to_particle(grid, positions, -1.0, -0.5, 2.0 / 7.0, 1.0 / 5.0)

    np.testing.assert_allclose(
        actual,
        np.broadcast_to(np.asarray([1.25, -0.375], dtype=dtype), actual.shape),
        rtol=1.0e-6,
        atol=1.0e-6,
    )
