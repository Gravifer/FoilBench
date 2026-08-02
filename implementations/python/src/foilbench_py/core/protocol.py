"""Structural solver interface."""

from typing import Protocol, runtime_checkable

from foilbench_py.core.geometry import NacaFoil
from foilbench_py.core.models import (
    CanonicalFlowState,
    ControlState,
    Diagnostics,
    ImportOutcome,
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

    def initialize(self, scenario: Scenario, geometry: NacaFoil, seed: int) -> None: ...

    def set_reynolds(self, reynolds: float) -> None: ...

    def advance(self, control: ControlState, target_dt: float) -> StepReport: ...

    def sample_velocity(self, points: PointCloud) -> PointCloud: ...

    def export_state(self) -> CanonicalFlowState: ...

    def import_state(self, state: CanonicalFlowState, control: ControlState) -> ImportOutcome: ...

    def diagnostics(self) -> Diagnostics: ...
