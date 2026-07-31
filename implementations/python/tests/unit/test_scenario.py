from dataclasses import replace
from pathlib import Path

import pytest

from foilbench_py.core.geometry import NacaFoil
from foilbench_py.core.models import DomainSpec
from foilbench_py.core.scenario import find_repo_root, load_scenario
from foilbench_py.solvers.factory import create_solver, solver_ids
from tests.helpers import ScenarioFactory


def test_default_scenario_validates() -> None:
    root = find_repo_root(Path(__file__))
    scenario = load_scenario(root / "scenarios" / "airfoil" / "default.json")
    assert scenario.id == "naca2412-dynamic"
    assert scenario.domain.resolution == (160, 96)


@pytest.mark.parametrize("solver_id", solver_ids())
def test_phase_one_solver_rejects_thin_3d(
    solver_id: str,
    scenario_factory: ScenarioFactory,
) -> None:
    scenario = scenario_factory()
    three_dimensional = DomainSpec(
        dimension=3,
        bounds=((-2.0, 6.0), (-2.0, 2.0), (0.0, 0.2)),
        resolution=(32, 16, 4),
        periodic_axes=("z",),
    )
    scenario = replace(
        scenario,
        domain=three_dimensional,
        freestream=(1.0, 0.0, 0.0),
        foil=replace(scenario.foil, pivot=(0.0, 0.0, 0.0)),
    )
    solver = create_solver(solver_id)
    assert solver.info.dimensions == (2,)
    with pytest.raises(NotImplementedError, match="only 2D"):
        solver.initialize(
            scenario,
            NacaFoil(scenario.foil),
            0,
        )
