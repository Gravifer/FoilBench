"""Atomic direct warm switching among solvers in one implementation."""

from collections.abc import Callable
from dataclasses import replace

from foilbench_py.core.geometry import NacaFoil
from foilbench_py.core.models import ControlKeyframe, ControlState, ImportReport, Scenario
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
        self._reynolds = scenario.reynolds
        self._solver = factory(initial_solver)
        self._solver.initialize(scenario, geometry, scenario.seed)
        self._last_import: ImportReport | None = None

    @property
    def solver(self) -> FlowSolver:
        return self._solver

    @property
    def last_import(self) -> ImportReport | None:
        return self._last_import

    @property
    def reynolds(self) -> float:
        return self._reynolds

    def set_reynolds(self, reynolds: float) -> None:
        self._solver.set_reynolds(reynolds)
        self._reynolds = self._solver.reynolds

    def switch(self, destination: str, control: ControlState) -> ImportReport:
        if destination == self._solver.info.id:
            return ImportReport(destination, destination, ())
        state = self._solver.export_state()
        candidate = self._factory(destination)
        candidate.initialize(self._scenario, self._geometry, self._scenario.seed)
        candidate.set_reynolds(self._reynolds)
        report = candidate.import_state(state, control)
        candidate.diagnostics()
        self._solver = candidate
        self._last_import = report
        return report

    def restart_at(self, control: ControlState) -> None:
        """Atomically replace the active solver with a fresh state at ``control``."""
        solver_id = self._solver.info.id
        initialization_scenario = replace(
            self._scenario,
            controls=(ControlKeyframe(0.0, control.angle_degrees),),
        )
        candidate = self._factory(solver_id)
        candidate.initialize(
            initialization_scenario,
            self._geometry,
            self._scenario.seed,
        )
        candidate.set_reynolds(self._reynolds)
        candidate.diagnostics()
        state = candidate.export_state()
        if state.source_solver != solver_id:
            raise RuntimeError(
                f"fresh {solver_id!r} solver exported state for {state.source_solver!r}"
            )
        self._solver = candidate
        self._last_import = None
