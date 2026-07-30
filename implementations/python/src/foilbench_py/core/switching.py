"""Atomic direct warm switching among solvers in one implementation."""

from collections.abc import Callable

from foilbench_py.core.geometry import NacaFoil
from foilbench_py.core.models import ControlState, ImportReport, Scenario
from foilbench_py.core.protocol import FlowSolver

SolverFactory = Callable[[str], FlowSolver]


class SolverManager:
    def __init__(
        self,
        factory: SolverFactory,
        scenario: Scenario,
        geometry: NacaFoil,
        initial_solver: str,
    ) -> None:
        self._factory = factory
        self._scenario = scenario
        self._geometry = geometry
        self._solver = factory(initial_solver)
        self._solver.initialize(scenario, geometry, scenario.seed)
        self._last_import: ImportReport | None = None

    @property
    def solver(self) -> FlowSolver:
        return self._solver

    @property
    def last_import(self) -> ImportReport | None:
        return self._last_import

    def switch(self, destination: str, control: ControlState) -> ImportReport:
        if destination == self._solver.info.id:
            return ImportReport(destination, destination, ())
        state = self._solver.export_state()
        candidate = self._factory(destination)
        candidate.initialize(self._scenario, self._geometry, self._scenario.seed)
        report = candidate.import_state(state, control)
        candidate.diagnostics()
        self._solver = candidate
        self._last_import = report
        return report
