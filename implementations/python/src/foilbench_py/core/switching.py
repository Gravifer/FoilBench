"""Atomic direct warm switching among solvers in one implementation."""

from collections.abc import Callable
from dataclasses import replace
from time import perf_counter

from foilbench_py.core.geometry import NacaFoil
from foilbench_py.core.models import (
    CanonicalFlowState,
    ControlKeyframe,
    ControlState,
    ImportFailureReason,
    ImportOutcome,
    ImportReport,
    NumericalFailure,
    Scenario,
    StepReport,
)
from foilbench_py.core.protocol import FlowSolver

SolverFactory = Callable[[str], FlowSolver]


def classify_import_failure(
    error: ValueError | FloatingPointError | NumericalFailure,
) -> ImportFailureReason:
    if isinstance(error, NumericalFailure):
        return error.reason
    message = str(error).lower().replace("-", "")
    if "nonfinite" in message or "must be finite" in message or "nan" in message:
        return "nonfinite_state"
    if "density" in message:
        return "invalid_density"
    if "projection" in message or "pressure cg" in message:
        return "projection_failure"
    if "geometry" in message:
        return "incompatible_geometry"
    if any(token in message for token in ("resolution", "dimension", "domain", "bounds")):
        return "incompatible_domain"
    if any(token in message for token in ("velocity", "wall", "cfl", "mach")):
        return "excessive_velocity"
    if isinstance(error, FloatingPointError):
        return "nonfinite_state"
    return "unsupported_conversion"


def state_at_control(
    state: CanonicalFlowState,
    control: ControlState,
) -> CanonicalFlowState:
    return replace(
        state,
        time=control.time,
        angle_degrees=control.angle_degrees,
        angular_velocity_degrees=control.angular_velocity_degrees,
    )


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
        self._last_validation_report: StepReport | None = None
        self._last_validation_elapsed = 0.0

    @property
    def solver(self) -> FlowSolver:
        return self._solver

    @property
    def last_import(self) -> ImportReport | None:
        return self._last_import

    @property
    def last_validation_report(self) -> StepReport | None:
        return self._last_validation_report

    @property
    def last_validation_elapsed(self) -> float:
        return self._last_validation_elapsed

    @property
    def reynolds(self) -> float:
        return self._reynolds

    def set_reynolds(self, reynolds: float) -> None:
        self._solver.set_reynolds(reynolds)
        self._reynolds = self._solver.reynolds

    def switch(
        self,
        destination: str,
        control: ControlState,
        validation_control: ControlState,
        validation_dt: float,
    ) -> ImportOutcome:
        if destination == self._solver.info.id:
            report = ImportReport(destination, destination, ())
            self._last_validation_report = None
            self._last_validation_elapsed = 0.0
            return ImportOutcome("accepted", "none", report)
        try:
            state = self._solver.export_state()
            candidate = self._factory(destination)
            candidate.initialize(self._scenario, self._geometry, self._scenario.seed)
            candidate.set_reynolds(self._reynolds)
            report = candidate.import_state(state, control)
            candidate.diagnostics()
            started = perf_counter()
            validation_report = candidate.advance(validation_control, validation_dt)
            validation_elapsed = max(perf_counter() - started, 1.0e-9)
            candidate.diagnostics()
        except (ValueError, FloatingPointError, NumericalFailure) as error:
            self._last_validation_report = None
            self._last_validation_elapsed = 0.0
            return ImportOutcome(
                "rejected",
                classify_import_failure(error),
                warnings=(str(error),),
            )
        self._solver = candidate
        self._last_import = report
        self._last_validation_report = validation_report
        self._last_validation_elapsed = validation_elapsed
        return ImportOutcome("accepted", "none", report, report.warnings)

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
        state = state_at_control(candidate.export_state(), control)
        if state.source_solver != solver_id:
            raise RuntimeError(
                f"fresh {solver_id!r} solver exported state for {state.source_solver!r}"
            )
        candidate.import_state(state, control)
        candidate.diagnostics()
        self._solver = candidate
        self._last_import = None
        self._last_validation_report = None
        self._last_validation_elapsed = 0.0
