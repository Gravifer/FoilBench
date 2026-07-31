import numpy as np

from foilbench_py.core.models import DomainSpec
from foilbench_py.core.particle_population import (
    maintain_particle_population,
    particle_cell_counts,
)
from foilbench_py.core.rng import PCG32


def _domain() -> DomainSpec:
    return DomainSpec(
        dimension=2,
        bounds=((0.0, 1.0), (0.0, 1.0)),
        resolution=(4, 4),
        periodic_axes=(),
    )


def test_population_maintenance_deterministically_repairs_holes_and_clumps() -> None:
    domain = _domain()
    solid = np.zeros((domain.ny, domain.nx), dtype=np.bool_)
    solid[1, 2] = True
    particle_count = 4 * int(np.count_nonzero(~solid))
    original = np.empty((particle_count, 2), dtype=np.float32)
    original[:, 0] = 0.125
    original[:, 1] = 0.125

    first = original.copy()
    second = original.copy()
    first_report = maintain_particle_population(first, solid, domain, PCG32(17))
    second_report = maintain_particle_population(second, solid, domain, PCG32(17))

    np.testing.assert_array_equal(first_report.moved_indices, second_report.moved_indices)
    np.testing.assert_array_equal(first, second)
    counts = particle_cell_counts(first, domain).reshape(domain.ny, domain.nx)
    assert np.all(counts[~solid] == 4)
    assert np.all(counts[solid] == 0)


def test_population_maintenance_is_inactive_within_limits() -> None:
    domain = _domain()
    solid = np.zeros((domain.ny, domain.nx), dtype=np.bool_)
    positions = np.empty((domain.nx * domain.ny * 4, 2), dtype=np.float32)
    index = 0
    for cell_y in range(domain.ny):
        for cell_x in range(domain.nx):
            for offset in (0.35, 0.45, 0.55, 0.65):
                positions[index, 0] = (cell_x + offset) * domain.dx
                positions[index, 1] = (cell_y + offset) * domain.dy
                index += 1
    before = positions.copy()

    report = maintain_particle_population(positions, solid, domain, PCG32(3))

    assert report.moved_indices.size == 0
    np.testing.assert_array_equal(positions, before)
