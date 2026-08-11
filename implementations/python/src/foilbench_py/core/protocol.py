"""Structural solver interface."""

from typing import Protocol, runtime_checkable

from foilbench_py.core.geometry import NacaFoil
from foilbench_py.core.models import (
    CanonicalFlowState,
    ControlState,
    Diagnostics,
    ImportOutcome,
    InteractiveTuning,
    RestartState,
    ReynoldsOutcome,
    Scenario,
    SolverInfo,
    StepReport,
)
from foilbench_py.types import PointCloud


@runtime_checkable
class FlowSolver(Protocol):
    info: SolverInfo

    @property
    def reynolds(self) -> float: ...

    @property
    def state_revision(self) -> int: ...

    def initialize(self, scenario: Scenario, geometry: NacaFoil, seed: int) -> None: ...

    def restart(
        self,
        scenario: Scenario,
        geometry: NacaFoil,
        seed: int,
        start: RestartState,
    ) -> None: ...

    def set_reynolds(self, reynolds: float) -> ReynoldsOutcome: ...

    def advance(self, control: ControlState, target_dt: float) -> StepReport: ...

    def sample_velocity(self, points: PointCloud) -> PointCloud: ...

    def export_state(self) -> CanonicalFlowState: ...

    def import_state(self, state: CanonicalFlowState, control: ControlState) -> ImportOutcome: ...

    def diagnostics(self) -> Diagnostics: ...

    def interactive_tuning(self) -> InteractiveTuning | None: ...

    def adjust_interactive_tuning(self, direction: int) -> InteractiveTuning | None: ...

    def apply_interactive_tuning(self, value: str | float) -> InteractiveTuning | None: ...
