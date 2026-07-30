"""Solver registry."""

from foilbench_py.core.protocol import FlowSolver
from foilbench_py.solvers.lbm import LBMSolver
from foilbench_py.solvers.pic_flip import PicFlipSolver
from foilbench_py.solvers.stable_fluids import StableFluidsSolver

_SOLVERS = ("stable-fluids", "lbm-d2q9", "pic-flip")


def solver_ids() -> tuple[str, ...]:
    return _SOLVERS


def create_solver(solver_id: str) -> FlowSolver:
    if solver_id == "stable-fluids":
        return StableFluidsSolver()
    if solver_id == "lbm-d2q9":
        return LBMSolver()
    if solver_id == "pic-flip":
        return PicFlipSolver()
    raise KeyError(f"unknown solver {solver_id!r}; choose from {', '.join(_SOLVERS)}")
