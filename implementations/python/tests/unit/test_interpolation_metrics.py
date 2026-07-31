# pyright: reportPrivateImportUsage=false
import einx
import numpy as np
import pytest

from foilbench_py.core.interpolation import sample_vector
from foilbench_py.core.metrics import analyze_wake_probe, momentum, speed_squared
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


def test_wake_spectrum_recovers_a_coherent_shedding_frequency() -> None:
    sample_dt = 0.01
    time = np.arange(400, dtype=np.float64) * sample_dt
    samples = 0.2 + 0.7 * np.sin(2.0 * np.pi * 2.5 * time)

    spectrum = analyze_wake_probe(samples, sample_dt, chord=1.0, freestream_speed=2.0)

    assert spectrum.sample_count == 400
    assert spectrum.frequency_resolution == pytest.approx(0.25)
    assert spectrum.transverse_rms == pytest.approx(0.7 / np.sqrt(2.0), rel=0.01)
    assert spectrum.dominant_frequency == pytest.approx(2.5)
    assert spectrum.strouhal_number == pytest.approx(1.25)
    assert spectrum.dominant_power_fraction > 0.6


def test_wake_spectrum_reports_stationary_flow_without_a_false_frequency() -> None:
    samples = np.ones(32, dtype=np.float64)

    spectrum = analyze_wake_probe(samples, 0.1, chord=1.0, freestream_speed=1.0)

    assert spectrum.transverse_rms == 0.0
    assert spectrum.dominant_frequency == 0.0
    assert spectrum.strouhal_number == 0.0
    assert spectrum.dominant_power_fraction == 0.0
