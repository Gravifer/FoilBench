import numpy as np
import pytest

from foilbench_py.solvers._numba_adapter import grid_to_particle, particle_to_grid


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


def test_transfer_adapter_rejects_inconsistent_dtypes_and_dimensions() -> None:
    grid = np.zeros((4, 5, 2), dtype=np.float32)
    positions = np.zeros((3, 2), dtype=np.float64)
    with pytest.raises(TypeError, match="same dtype"):
        grid_to_particle(grid, positions, 0.0, 0.0, 0.2, 0.25)

    positions_3d = np.zeros((3, 3), dtype=np.float32)
    with pytest.raises((TypeError, ValueError)):
        grid_to_particle(grid, positions_3d, 0.0, 0.0, 0.2, 0.25)

    velocities = np.zeros((4, 2), dtype=np.float32)
    with pytest.raises((TypeError, ValueError)):
        particle_to_grid(
            positions_3d,
            velocities,
            0.0,
            0.0,
            0.2,
            0.25,
            5,
            4,
            (1.0, 0.0),
        )


@pytest.mark.parametrize("dtype", [np.float16, np.int32])
def test_transfer_adapter_rejects_unsupported_array_dtypes(
    dtype: type[np.float16] | type[np.int32],
) -> None:
    grid = np.zeros((4, 5, 2), dtype=dtype)
    positions = np.zeros((3, 2), dtype=dtype)
    with pytest.raises((TypeError, ValueError)):
        grid_to_particle(grid, positions, 0.0, 0.0, 0.2, 0.25)


def test_grid_to_particle_rejects_empty_grid_axes() -> None:
    grid = np.zeros((0, 5, 2), dtype=np.float32)
    positions = np.zeros((3, 2), dtype=np.float32)
    with pytest.raises((TypeError, ValueError)):
        grid_to_particle(grid, positions, 0.0, 0.0, 0.2, 0.25)


def test_periodic_transfer_wraps_quadratic_support() -> None:
    grid = np.arange(6 * 8 * 2, dtype=np.float32).reshape(6, 8, 2)
    x0 = -1.0
    y0 = -0.75
    dx = 0.25
    dy = 0.25
    width = grid.shape[1] * dx
    height = grid.shape[0] * dy
    positions = np.asarray(
        [
            [x0 - 0.07, y0 + 0.31],
            [x0 + width - 0.07, y0 + 0.31],
            [x0 + 0.43, y0 - 0.09],
            [x0 + 0.43, y0 + height - 0.09],
        ],
        dtype=np.float32,
    )

    gathered = grid_to_particle(
        grid,
        positions,
        x0,
        y0,
        dx,
        dy,
        periodic_x=True,
        periodic_y=True,
    )
    np.testing.assert_allclose(gathered[0], gathered[1], rtol=1.0e-6, atol=1.0e-6)
    np.testing.assert_allclose(gathered[2], gathered[3], rtol=1.0e-6, atol=1.0e-6)

    velocities = np.asarray(
        [[0.75, -0.25], [0.75, -0.25]],
        dtype=np.float32,
    )
    left = particle_to_grid(
        positions[:1],
        velocities[:1],
        x0,
        y0,
        dx,
        dy,
        grid.shape[1],
        grid.shape[0],
        (0.0, 0.0),
        periodic_x=True,
        periodic_y=True,
    )
    right = particle_to_grid(
        positions[1:2],
        velocities[1:2],
        x0,
        y0,
        dx,
        dy,
        grid.shape[1],
        grid.shape[0],
        (0.0, 0.0),
        periodic_x=True,
        periodic_y=True,
    )
    np.testing.assert_allclose(left, right, rtol=1.0e-6, atol=1.0e-6)
