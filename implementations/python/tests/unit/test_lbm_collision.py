# pyright: reportPrivateUsage=false

import numpy as np
import pytest

from foilbench_py.solvers._numba_adapter import (
    lbm_apply_sponge,
    lbm_moving_wall_stream,
    lbm_trt_collision,
)

_OPPOSITE = np.asarray([0, 3, 4, 1, 2, 7, 8, 5, 6], dtype=np.int64)
_W = np.asarray([4 / 9, 1 / 9, 1 / 9, 1 / 9, 1 / 9, 1 / 36, 1 / 36, 1 / 36, 1 / 36])
_C = np.asarray(
    [[0, 0], [1, 0], [0, 1], [-1, 0], [0, -1], [1, 1], [-1, 1], [-1, -1], [1, -1]],
    dtype=np.float64,
)


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
    equilibrium = (
        density[:, :, None]
        * _W[None, None, :]
        * (1.0 + 3.0 * projection + 4.5 * projection * projection - 1.5 * speed_squared[:, :, None])
    )
    delta = populations - equilibrium
    expected = (
        populations
        - 0.5 * (omega_plus + omega_minus) * delta
        - 0.5 * (omega_plus - omega_minus) * delta[:, :, _OPPOSITE]
    )

    actual_density, actual = lbm_trt_collision(populations, omega_plus, omega_minus)

    np.testing.assert_allclose(actual_density, density, rtol=2.0e-6)
    np.testing.assert_allclose(actual, expected, rtol=2.0e-5, atol=2.0e-6)


@pytest.mark.parametrize("dtype", [np.float32, np.float64])
def test_compiled_stream_matches_periodic_numpy_reference_without_solids(
    dtype: type[np.float32] | type[np.float64],
) -> None:
    rng = np.random.default_rng(23)
    post = np.asarray(rng.random((5, 7, 9)), dtype=dtype)
    density = np.asarray(rng.random((5, 7)) + 1.0, dtype=dtype)
    solid = np.zeros((5, 7), dtype=np.bool_)
    distance = np.ones((5, 7), dtype=dtype)
    actual = lbm_moving_wall_stream(
        post,
        density,
        solid,
        distance,
        ((0.0, 7.0), (0.0, 5.0)),
        (1.0, 1.0),
        (0.0, 0.0),
        0.0,
        1.0,
    )
    expected = np.empty_like(post)
    for direction, (cx, cy) in enumerate(_C.astype(np.int64)):
        expected[:, :, direction] = np.roll(
            post[:, :, direction], shift=(int(cy), int(cx)), axis=(0, 1)
        )
    np.testing.assert_array_equal(actual, expected)


def test_compiled_stream_matches_vectorized_moving_wall_reference() -> None:
    rng = np.random.default_rng(29)
    post = np.asarray(rng.random((6, 8, 9)), dtype=np.float64)
    density = np.asarray(rng.random((6, 8)) + 1.0, dtype=np.float64)
    yy, xx = np.mgrid[:6, :8]
    distance = np.hypot(xx + 0.5 - 3.5, yy + 0.5 - 2.5) - 1.35
    solid = distance < 0.0
    omega = 0.17
    scale = 0.08
    actual = lbm_moving_wall_stream(
        post,
        density,
        solid,
        distance,
        ((0.0, 8.0), (0.0, 6.0)),
        (1.0, 1.0),
        (3.0, 2.0),
        omega,
        scale,
    )
    expected = np.zeros_like(post)
    for direction, (cx_raw, cy_raw) in enumerate(_C.astype(np.int64)):
        cx, cy = int(cx_raw), int(cy_raw)
        if direction == 0:
            expected[:, :, 0] = post[:, :, 0]
            continue
        destination_solid = np.roll(solid, shift=(-cy, -cx), axis=(0, 1))
        wall_link = ~solid & destination_solid
        outgoing = np.where(~solid & ~destination_solid, post[:, :, direction], 0.0)
        expected[:, :, direction] += np.roll(outgoing, shift=(cy, cx), axis=(0, 1))
        if not np.any(wall_link):
            continue
        destination_distance = np.roll(distance, shift=(-cy, -cx), axis=(0, 1))
        fraction = np.clip(
            distance / np.maximum(distance - destination_distance, 1.0e-12), 0.05, 1.0
        )
        q = fraction[wall_link]
        source = post[:, :, direction][wall_link]
        opposite = int(_OPPOSITE[direction])
        reflected = np.empty_like(source)
        near = q < 0.5
        upstream = np.roll(post[:, :, direction], shift=(cy, cx), axis=(0, 1))[wall_link]
        reflected[near] = 2.0 * q[near] * source[near] + (1.0 - 2.0 * q[near]) * upstream[near]
        far_q = q[~near]
        reflected[~near] = source[~near] / (2.0 * far_q) + (2.0 * far_q - 1.0) * post[
            :, :, opposite
        ][wall_link][~near] / (2.0 * far_q)
        wall_x = xx[wall_link] + 0.5 + q * cx
        wall_y = yy[wall_link] + 0.5 + q * cy
        wall_u = -omega * (wall_y - 2.0)
        wall_v = omega * (wall_x - 3.0)
        reflected -= 6.0 * _W[direction] * density[wall_link] * (cx * wall_u + cy * wall_v) * scale
        expected[:, :, opposite][wall_link] = reflected
    np.testing.assert_allclose(actual, expected, rtol=1.0e-13, atol=1.0e-13)


@pytest.mark.parametrize("dtype", [np.float32, np.float64])
def test_compiled_sponge_matches_numpy_reference(
    dtype: type[np.float32] | type[np.float64],
) -> None:
    rng = np.random.default_rng(31)
    populations = np.asarray(rng.random((5, 7, 9)), dtype=dtype)
    equilibrium = np.asarray(rng.random((5, 7, 9)), dtype=dtype)
    strength = np.asarray(rng.random((5, 7)) * 0.2, dtype=dtype)
    expected = (1.0 - strength[:, :, None]) * populations + strength[:, :, None] * equilibrium
    lbm_apply_sponge(populations, equilibrium, strength)
    np.testing.assert_allclose(populations, expected, rtol=2.0e-6, atol=2.0e-7)
