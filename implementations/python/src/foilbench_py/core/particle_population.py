"""Deterministic population maintenance for full-domain particle solvers."""

from dataclasses import dataclass

import numpy as np
from jaxtyping import Integer

from foilbench_py.core.models import DomainSpec
from foilbench_py.core.rng import PCG32
from foilbench_py.types import MaskField, PointCloud


@dataclass(frozen=True, slots=True)
class PopulationMaintenanceReport:
    moved_indices: Integer[np.ndarray, " moved"]
    counts: Integer[np.ndarray, " cell"]


def particle_cell_ids(
    positions: PointCloud,
    domain: DomainSpec,
) -> Integer[np.ndarray, " particle"]:
    cell_x = np.floor(
        (positions[:, 0] - domain.bounds[0][0]) / domain.dx
    ).astype(np.int64)
    cell_y = np.floor(
        (positions[:, 1] - domain.bounds[1][0]) / domain.dy
    ).astype(np.int64)
    cell_x = np.clip(cell_x, 0, domain.nx - 1)
    cell_y = np.clip(cell_y, 0, domain.ny - 1)
    return cell_y * domain.nx + cell_x


def particle_cell_counts(
    positions: PointCloud,
    domain: DomainSpec,
) -> Integer[np.ndarray, " cell"]:
    return np.bincount(
        particle_cell_ids(positions, domain),
        minlength=domain.nx * domain.ny,
    ).astype(np.int64, copy=False)


def maintain_particle_population(
    positions: PointCloud,
    solid: MaskField,
    domain: DomainSpec,
    rng: PCG32,
    minimum: int = 2,
    target: int = 4,
    maximum: int = 8,
) -> PopulationMaintenanceReport:
    if not 0 < minimum <= target <= maximum:
        raise ValueError("particle population limits must satisfy 0 < minimum <= target <= maximum")
    if positions.shape[0] == 0:
        raise ValueError("particle population cannot be empty")

    cell_count = domain.nx * domain.ny
    cell_ids = particle_cell_ids(positions, domain)
    counts = np.bincount(cell_ids, minlength=cell_count).astype(np.int64, copy=False)
    fluid_ids = np.flatnonzero(~solid.reshape(-1))
    if fluid_ids.size == 0:
        raise ValueError("particle population requires at least one fluid cell")

    fluid_counts = counts[fluid_ids]
    solid_occupied = bool(np.any(counts[solid.reshape(-1)]))
    within_limits = bool(
        np.all((fluid_counts >= minimum) & (fluid_counts <= maximum))
    )
    if within_limits and not solid_occupied:
        return PopulationMaintenanceReport(np.empty(0, dtype=np.int64), counts)

    desired = counts.copy()
    desired[solid.reshape(-1)] = 0
    underfilled = fluid_ids[counts[fluid_ids] < minimum]
    overfilled = fluid_ids[counts[fluid_ids] > maximum]
    desired[underfilled] = target
    desired[overfilled] = target

    population_difference = positions.shape[0] - int(np.sum(desired))
    if population_difference > 0:
        capacity = maximum - desired[fluid_ids]
        priority = np.lexsort((fluid_ids, desired[fluid_ids]))
        destination_slots = np.repeat(
            fluid_ids[priority],
            capacity[priority],
        )
        if destination_slots.size < population_difference:
            raise RuntimeError("particle count exceeds configured maximum population")
        np.add.at(desired, destination_slots[:population_difference], 1)
    elif population_difference < 0:
        removable = desired[fluid_ids] - minimum
        priority = np.lexsort((fluid_ids, -desired[fluid_ids]))
        donor_slots = np.repeat(
            fluid_ids[priority],
            removable[priority],
        )
        removal_count = -population_difference
        if donor_slots.size < removal_count:
            raise RuntimeError("particle count cannot satisfy configured minimum population")
        np.subtract.at(desired, donor_slots[:removal_count], 1)

    order = np.argsort(cell_ids, kind="stable")
    sorted_cells = cell_ids[order]
    sequence = np.arange(order.size, dtype=np.int64)
    group_start = np.zeros(order.size, dtype=np.int64)
    new_group = np.empty(order.size, dtype=np.bool_)
    new_group[0] = True
    new_group[1:] = sorted_cells[1:] != sorted_cells[:-1]
    group_start[new_group] = sequence[new_group]
    np.maximum.accumulate(group_start, out=group_start)
    rank_in_cell = sequence - group_start
    donor_indices = order[rank_in_cell >= desired[sorted_cells]]

    deficits = desired - np.minimum(counts, desired)
    destination_cells = np.repeat(
        np.arange(cell_count, dtype=np.int64),
        deficits,
    )
    if donor_indices.size != destination_cells.size:
        raise RuntimeError("particle population redistribution is unbalanced")

    if donor_indices.size:
        destination_x = destination_cells % domain.nx
        destination_y = destination_cells // domain.nx
        jitter = rng.random((donor_indices.size, 2)).astype(positions.dtype, copy=False)
        positions[donor_indices, 0] = domain.bounds[0][0] + (
            destination_x + 0.4 + 0.2 * jitter[:, 0]
        ) * domain.dx
        positions[donor_indices, 1] = domain.bounds[1][0] + (
            destination_y + 0.4 + 0.2 * jitter[:, 1]
        ) * domain.dy

    final_counts = particle_cell_counts(positions, domain)
    return PopulationMaintenanceReport(donor_indices, final_counts)
