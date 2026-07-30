import numpy as np

from foilbench_py.core.rng import PCG32


def test_pcg32_is_reproducible() -> None:
    first = PCG32(42, stream=54)
    second = PCG32(42, stream=54)
    values_first = [first.next_uint32() for _ in range(8)]
    values_second = [second.next_uint32() for _ in range(8)]
    assert values_first == values_second
    assert values_first == [
        2707161783,
        2068313097,
        3122475824,
        2211639955,
        3215226955,
        3421331566,
        3217466285,
        2167406445,
    ]


def test_pcg32_float_range_and_shape() -> None:
    values = PCG32(7).random((3, 5))
    assert values.shape == (3, 5)
    assert values.dtype == np.float32
    assert np.all((values >= 0.0) & (values < 1.0))
