import numpy as np

from foilbench_py.core.geometry import NacaFoil
from foilbench_py.core.models import FoilSpec


def test_naca_distance_sign_and_normals() -> None:
    foil = NacaFoil(FoilSpec("0012", 1.0, (0.0, 0.0)))
    points = np.asarray([[0.25, 0.0], [0.25, 0.4]], dtype=np.float64)
    distance = foil.signed_distance(points, 0.0)
    normals = foil.normals(points, 0.0)
    assert distance[0] < 0.0
    assert distance[1] > 0.0
    np.testing.assert_allclose(np.linalg.norm(normals, axis=1), 1.0, atol=1.0e-4)


def test_outline_rotates_about_quarter_chord() -> None:
    foil = NacaFoil(FoilSpec("2412", 1.0, (0.0, 0.0)))
    horizontal = foil.outline(0.0, samples=64)
    rotated = foil.outline(20.0, samples=64)
    assert horizontal.shape == rotated.shape == (64, 2)
    assert not np.allclose(horizontal, rotated)
