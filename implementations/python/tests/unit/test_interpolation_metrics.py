# pyright: reportPrivateImportUsage=false
import einx
import numpy as np

from foilbench_py.core.interpolation import sample_vector
from foilbench_py.core.metrics import momentum, speed_squared
from foilbench_py.core.models import DomainSpec


def test_bilinear_sampling_constant_field() -> None:
    domain = DomainSpec(2, ((0.0, 2.0), (0.0, 1.0)), (8, 4))
    velocity = np.empty((4, 8, 2), dtype=np.float64)
    velocity[...] = (2.0, -0.5)
    points = np.asarray([[0.1, 0.1], [1.0, 0.5], [1.9, 0.9]])
    sampled = sample_vector(velocity, points, domain)
    np.testing.assert_allclose(sampled, np.asarray([[2.0, -0.5]] * 3))


def test_einx_metrics_match_numpy_on_multiple_shapes() -> None:
    for shape in ((3, 5, 2), (7, 4, 2)):
        velocity = np.arange(np.prod(shape), dtype=np.float64).reshape(shape) / 10.0
        expected_speed = np.sum(velocity * velocity, axis=2)
        expected_momentum = np.mean(velocity, axis=(0, 1))
        np.testing.assert_allclose(speed_squared(velocity), expected_speed)
        np.testing.assert_allclose(momentum(velocity), expected_momentum)
        graph = einx.sum("ny nx [component] -> ny nx", velocity * velocity, graph=True)
        assert graph is not None
