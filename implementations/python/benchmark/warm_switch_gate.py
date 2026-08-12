"""Revision 4 full-resolution warm-switch and fresh-fallback gate."""

import itertools
import json
from collections.abc import Callable
from pathlib import Path
from typing import cast

import numpy as np

from foilbench_py.core.geometry import NacaFoil
from foilbench_py.core.models import (
    CanonicalFlowState,
    ControlState,
    Diagnostics,
    ImportOutcome,
    InteractiveTuning,
    NumericalFailure,
    RestartState,
    ReynoldsOutcome,
    Scenario,
    SolverInfo,
    StepReport,
)
from foilbench_py.core.protocol import FlowSolver
from foilbench_py.core.scenario import find_repo_root, load_scenario
from foilbench_py.core.switching import SolverManager
from foilbench_py.solvers.factory import create_solver
from foilbench_py.types import PointCloud
from foilbench_py.viewer.app import ViewerModel


class DelegatingSolver:
    def __init__(self, inner: FlowSolver) -> None:
        self.inner = inner
        self.info: SolverInfo = inner.info

    @property
    def reynolds(self) -> float:
        return self.inner.reynolds

    @property
    def state_revision(self) -> int:
        return self.inner.state_revision

    def initialize(self, scenario: Scenario, geometry: NacaFoil, seed: int) -> None:
        self.inner.initialize(scenario, geometry, seed)

    def restart(
        self,
        scenario: Scenario,
        geometry: NacaFoil,
        seed: int,
        start: RestartState,
    ) -> None:
        self.inner.restart(scenario, geometry, seed, start)

    def set_reynolds(self, reynolds: float) -> ReynoldsOutcome:
        return self.inner.set_reynolds(reynolds)

    def advance(self, control: ControlState, target_dt: float) -> StepReport:
        return self.inner.advance(control, target_dt)

    def sample_velocity(self, points: PointCloud) -> PointCloud:
        return self.inner.sample_velocity(points)

    def export_state(self) -> CanonicalFlowState:
        return self.inner.export_state()

    def import_state(
        self, state: CanonicalFlowState, control: ControlState
    ) -> ImportOutcome:
        return self.inner.import_state(state, control)

    def diagnostics(self) -> Diagnostics:
        return self.inner.diagnostics()

    def interactive_tuning(self) -> InteractiveTuning | None:
        return self.inner.interactive_tuning()

    def adjust_interactive_tuning(self, direction: int) -> InteractiveTuning | None:
        return self.inner.adjust_interactive_tuning(direction)

    def apply_interactive_tuning(self, value: str | float) -> InteractiveTuning | None:
        return self.inner.apply_interactive_tuning(value)


class RejectingImportSolver(DelegatingSolver):
    def import_state(
        self, state: CanonicalFlowState, control: ControlState
    ) -> ImportOutcome:
        del state, control
        return ImportOutcome(
            "rejected",
            "nonfinite_state",
            stage="canonical-import",
        )


class FailingAdvanceSolver(DelegatingSolver):
    def advance(self, control: ControlState, target_dt: float) -> StepReport:
        del control, target_dt
        raise NumericalFailure(
            "stability_limit",
            "injected fresh-fallback validation failure",
            "time-mapping",
        )


def fallback_factory(
    destination: str, *, fail_fresh_step: bool
) -> tuple[Callable[[str], FlowSolver], list[int]]:
    destination_creations = [0]

    def factory(solver_id: str) -> FlowSolver:
        solver = create_solver(solver_id)
        if solver_id != destination:
            return solver
        destination_creations[0] += 1
        if destination_creations[0] == 1:
            return RejectingImportSolver(solver)
        if fail_fresh_step:
            return FailingAdvanceSolver(solver)
        return solver

    return factory, destination_creations


def validate_fallback(
    scenario: Scenario,
    angle: float,
    source: str,
    destination: str,
    *,
    fail_fresh_step: bool,
) -> None:
    factory, destination_creations = fallback_factory(
        destination, fail_fresh_step=fail_fresh_step
    )
    model = ViewerModel.create(scenario, source, factory)
    model.set_angle(angle, 1.0)
    model.update(scenario.output_dt)
    source_solver = model.manager.solver
    source_time = model.time
    source_epoch = model.solver_epoch
    source_positions = model.tracers.positions.copy()
    source_generations = model.tracers.generations.copy()
    source_counters = dict(model.tracers.recycle_counters)
    source_reynolds = model.manager.reynolds

    outcome = model.switch_solver(destination)
    if fail_fresh_step:
        if outcome.accepted:
            raise RuntimeError("fresh fallback committed a failing tentative destination")
        if model.manager.solver is not source_solver:
            raise RuntimeError("failed fresh fallback replaced the valid source")
        if model.time != source_time or model.solver_epoch != source_epoch:
            raise RuntimeError("failed fresh fallback changed time or solver epoch")
        np.testing.assert_array_equal(model.tracers.positions, source_positions)
        np.testing.assert_array_equal(model.tracers.generations, source_generations)
        if model.tracers.recycle_counters != source_counters:
            raise RuntimeError("failed fresh fallback changed tracer counters")
    else:
        if not outcome.accepted or model.manager.solver.info.id != destination:
            raise RuntimeError("fresh fallback did not commit its validated destination")
        if model.time <= source_time or model.solver_epoch != source_epoch + 1:
            raise RuntimeError("successful fresh fallback did not commit one destination step")
        if (
            model.recovery_reason != "nonfinite_state"
            or model.recovery_stage != "warm-import-fallback"
        ):
            raise RuntimeError("successful fresh fallback omitted recovery telemetry")
        if model.manager.reynolds != source_reynolds:
            raise RuntimeError("successful fresh fallback changed selected Reynolds number")
        if not np.isfinite(model.manager.solver.export_state().velocity).all():
            raise RuntimeError("successful fresh fallback produced a non-finite state")
        expected_recycles = source_counters["forced_recovery"] + model.tracers.positions.shape[0]
        if model.tracers.recycle_counters["forced_recovery"] != expected_recycles:
            raise RuntimeError("successful fresh fallback did not reseed tracers exactly once")
    if destination_creations[0] != 2:
        raise RuntimeError("fresh fallback did not construct exactly two destinations")


def main() -> None:
    root = find_repo_root(Path(__file__))
    fixture = cast(
        dict[str, object],
        json.loads(
            (root / "spec/conformance/fullsize-acceptance.json").read_text(
                encoding="utf-8"
            )
        ),
    )
    gate = cast(dict[str, object], fixture["warm_switch"])
    scenario = load_scenario(root / str(gate["scenario"]))
    resolution = tuple(cast(list[int], gate["resolution"]))
    if scenario.domain.resolution != resolution:
        raise ValueError("warm-switch fixture resolution disagrees with its scenario")
    if gate.get("require_all_directed_pairs") is not True:
        raise ValueError("Revision 4 requires every directed warm-switch pair")
    if gate.get("validate_fresh_fallback_first_step") is not True:
        raise ValueError("Revision 4 requires tentative fresh-fallback validation")
    solvers = ("stable-fluids", "lbm-d2q9", "pic-flip")
    for angle, (source, destination) in itertools.product(
        cast(list[float], gate["angles_degrees"]),
        itertools.permutations(solvers, 2),
    ):
        manager = SolverManager(create_solver, scenario, NacaFoil(scenario.foil), source)
        source_control = ControlState(scenario.output_dt, angle, 0.0)
        manager.solver.advance(source_control, scenario.output_dt)
        validation = ControlState(2.0 * scenario.output_dt, angle, 0.0)
        outcome = manager.switch(destination, source_control, validation, scenario.output_dt)
        state = manager.solver.export_state()
        if not outcome.accepted or not np.isfinite(state.velocity).all():
            raise RuntimeError(f"warm switch failed: {source} -> {destination} at {angle}")
        print(f"passed warm {source} -> {destination} at {angle:.1f} degrees")

    for angle, destination in itertools.product(
        cast(list[float], gate["angles_degrees"]), solvers
    ):
        source = solvers[(solvers.index(destination) + 1) % len(solvers)]
        validate_fallback(scenario, angle, source, destination, fail_fresh_step=False)
        validate_fallback(scenario, angle, source, destination, fail_fresh_step=True)
        print(f"passed fresh fallback transactions -> {destination} at {angle:.1f} degrees")


if __name__ == "__main__":
    main()
