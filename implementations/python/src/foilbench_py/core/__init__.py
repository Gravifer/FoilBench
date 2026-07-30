"""Shared physical and numerical foundations."""

from foilbench_py.core.models import (
    CanonicalFlowState,
    ControlKeyframe,
    ControlState,
    Diagnostics,
    DomainSpec,
    FoilSpec,
    ImportReport,
    Scenario,
    SolverInfo,
    StepReport,
)
from foilbench_py.core.protocol import FlowSolver

__all__ = [
    "CanonicalFlowState",
    "ControlKeyframe",
    "ControlState",
    "Diagnostics",
    "DomainSpec",
    "FlowSolver",
    "FoilSpec",
    "ImportReport",
    "Scenario",
    "SolverInfo",
    "StepReport",
]
