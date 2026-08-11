# pyright: reportPrivateUsage=false

import json
import math
from dataclasses import replace
from pathlib import Path
from typing import TypedDict, cast

import numpy as np
import pytest

from foilbench_py.core.geometry import NacaFoil
from foilbench_py.core.models import CanonicalFlowState, ControlState, NumericalFailure
from foilbench_py.core.protocol import FlowSolver
from foilbench_py.core.scenario import load_scenario
from foilbench_py.solvers.factory import create_solver, solver_ids
from foilbench_py.solvers.lbm import LBMSolver
from foilbench_py.solvers.pic_flip import PicFlipSolver
from foilbench_py.solvers.stable_fluids import StableFluidsSolver
from tests.helpers import ScenarioFactory


class _ValidityFixture(TypedDict):
    contract_id: str
    contract_revision: int
    scenario: str
    resolution: list[int]
    target_dt: float
    changed_reynolds: float
    invalid_completion_time: float
    invalid_completion_reason: str
    invalid_completion_stage: str
    extreme_angular_velocity_degrees: float
    maximum_internal_substeps: int
    extreme_motion_allowed_reasons: list[str]
    accepted_evidence: dict[str, list[str]]
    limits: dict[str, float]


_REPOSITORY_ROOT = Path(__file__).resolve().parents[4]


def _validity_fixture() -> _ValidityFixture:
    return cast(
        _ValidityFixture,
        json.loads(
            (_REPOSITORY_ROOT / "spec/conformance/solver-validity.json").read_text(
                encoding="utf-8"
            )
        ),
    )


def _assert_same_canonical_state(
    first: CanonicalFlowState,
    second: CanonicalFlowState,
) -> None:
    assert first.schema_version == second.schema_version
    assert first.dimension == second.dimension
    assert first.bounds == second.bounds
    assert first.resolution == second.resolution
    assert first.periodic_axes == second.periodic_axes
    assert first.time == second.time
    assert first.precision == second.precision
    assert first.angle_degrees == second.angle_degrees
    assert first.angular_velocity_degrees == second.angular_velocity_degrees
    np.testing.assert_array_equal(first.velocity, second.velocity)
    if first.density is None:
        assert second.density is None
    else:
        assert second.density is not None
        np.testing.assert_array_equal(first.density, second.density)


@pytest.mark.parametrize("solver_id", solver_ids())
def test_solver_protocol_and_canonical_export(
    solver_id: str, scenario_factory: ScenarioFactory
) -> None:
    scenario = scenario_factory()
    geometry = NacaFoil(scenario.foil)
    solver = create_solver(solver_id)
    assert isinstance(solver, FlowSolver)
    solver.initialize(scenario, geometry, scenario.seed)
    report = solver.advance(scenario.control_at(0.01), 0.01)
    points = np.asarray([[-1.0, 0.0], [1.0, 0.5]], dtype=np.float32)
    sampled = solver.sample_velocity(points)
    state = solver.export_state()
    diagnostics = solver.diagnostics()
    assert report.advanced_dt == pytest.approx(0.01)
    assert report.substeps >= 1
    assert report.state_revision == 1
    assert sampled.shape == (2, 2)
    assert np.isfinite(sampled).all()
    assert state.velocity.shape == (1, scenario.domain.ny, scenario.domain.nx, 2)
    solid = geometry.mask(scenario.domain, state.angle_degrees)
    np.testing.assert_array_equal(state.velocity[0][solid], 0.0)
    assert all(np.isfinite(value) for value in diagnostics.values.values())
    assert diagnostics.state_revision == report.state_revision


def test_lbm_import_ignores_finite_solid_density(
    scenario_factory: ScenarioFactory,
) -> None:
    scenario = scenario_factory(resolution=(64, 32))
    geometry = NacaFoil(scenario.foil)
    source = LBMSolver()
    source.initialize(scenario, geometry, scenario.seed)
    state = source.export_state()
    assert state.density is not None
    solid = geometry.mask(scenario.domain, state.angle_degrees)
    density = state.density.copy()
    density[0][solid] = 100.0
    destination = LBMSolver()
    destination.initialize(scenario, geometry, scenario.seed)

    outcome = destination.import_state(
        replace(state, density=density),
        ControlState(
            state.time,
            state.angle_degrees,
            state.angular_velocity_degrees,
        ),
    )

    assert outcome.status == "accepted"


def test_shared_revision_3_validity_fixture() -> None:
    fixture = _validity_fixture()
    assert fixture["contract_id"] == "foilbench-phase2-v1"
    assert fixture["contract_revision"] == 3
    scenario = load_scenario(_REPOSITORY_ROOT / fixture["scenario"])
    resolution = fixture["resolution"]
    scenario = replace(
        scenario,
        domain=replace(scenario.domain, resolution=(resolution[0], resolution[1])),
    )
    geometry = NacaFoil(scenario.foil)

    for solver_id in solver_ids():
        solver = create_solver(solver_id)
        solver.initialize(scenario, geometry, scenario.seed)
        assert solver.state_revision == 0
        solver.set_reynolds(fixture["changed_reynolds"])
        assert solver.state_revision == 1
        solver.set_reynolds(fixture["changed_reynolds"])
        assert solver.state_revision == 1
        report = solver.advance(
            scenario.control_at(fixture["target_dt"]),
            fixture["target_dt"],
        )
        for key in fixture["accepted_evidence"][solver_id]:
            assert key in report.evidence
            value = report.evidence[key]
            if isinstance(value, bool):
                if key.endswith("_converged"):
                    assert value
            else:
                assert math.isfinite(float(value))
        assert report.state_revision == solver.state_revision == 2
        assert solver.diagnostics().state_revision == report.state_revision

        if solver_id == "stable-fluids":
            assert float(report.evidence["maximum_characteristic_displacement"]) <= (
                fixture["limits"]["stable_maximum_characteristic_displacement"]
            )
            assert float(report.evidence["maximum_boundary_sweep"]) <= fixture["limits"][
                "stable_maximum_boundary_sweep"
            ]
        elif solver_id == "lbm-d2q9":
            assert float(report.evidence["maximum_lattice_mach"]) <= (
                fixture["limits"]["lbm_maximum_mach"] * (1.0 + 1.0e-6)
            )
            assert float(report.evidence["density_excursion"]) <= fixture["limits"][
                "lbm_maximum_density_excursion"
            ]
            assert float(report.evidence["minimum_population"]) >= fixture["limits"][
                "lbm_minimum_population"
            ]
            assert float(report.evidence["trt_magic"]) == pytest.approx(
                fixture["limits"]["lbm_trt_magic"]
            )
        elif solver_id == "pic-flip":
            assert float(report.evidence["maximum_particle_cfl"]) <= (
                fixture["limits"]["pic_maximum_particle_cfl"] * (1.0 + 1.0e-6)
            )
            assert float(report.evidence["empty_cell_fraction"]) <= fixture["limits"][
                "pic_maximum_empty_cell_fraction"
            ]
            assert float(report.evidence["underfilled_cell_fraction"]) <= fixture[
                "limits"
            ]["pic_maximum_underfilled_cell_fraction"]
            assert float(report.evidence["unsupported_face_fraction"]) <= fixture[
                "limits"
            ]["pic_maximum_unsupported_face_fraction"]
            assert int(report.evidence["unresolved_solid_particles"]) <= fixture[
                "limits"
            ]["pic_maximum_unresolved_solid_particles"]
        if solver_id in {"stable-fluids", "pic-flip"}:
            assert float(report.evidence["pressure_relative_residual"]) <= fixture[
                "limits"
            ]["pressure_maximum_relative_residual"]
            assert float(report.evidence["viscosity_final_residual"]) <= fixture[
                "limits"
            ]["viscosity_maximum_final_residual"]
            assert float(report.evidence["divergence_linf"]) <= fixture["limits"][
                "mac_maximum_divergence_linf"
            ]
            assert float(report.evidence["solid_leakage"]) <= fixture["limits"][
                "mac_maximum_solid_leakage"
            ]
        diagnostic_values = solver.diagnostics().values
        assert np.isfinite(diagnostic_values["solid_leakage"])
        if solver_id in {"stable-fluids", "pic-flip"}:
            assert np.isfinite(diagnostic_values["divergence_linf"])
        else:
            assert "divergence_linf" not in diagnostic_values
            assert diagnostic_values["solid_leakage"] == 0.0
            assert np.isfinite(diagnostic_values["cut_link_adjacent_normal_speed"])

        mismatch = create_solver(solver_id)
        mismatch.initialize(scenario, geometry, scenario.seed)
        before = mismatch.export_state()
        with pytest.raises(NumericalFailure) as captured:
            mismatch.advance(
                ControlState(fixture["invalid_completion_time"], 0.0, 0.0),
                fixture["target_dt"],
            )
        assert captured.value.reason == fixture["invalid_completion_reason"]
        assert captured.value.stage == fixture["invalid_completion_stage"]
        _assert_same_canonical_state(before, mismatch.export_state())

        extreme = create_solver(solver_id)
        extreme.initialize(scenario, geometry, scenario.seed)
        before = extreme.export_state()
        with pytest.raises(NumericalFailure) as captured:
            extreme.advance(
                ControlState(
                    fixture["target_dt"],
                    0.0,
                    fixture["extreme_angular_velocity_degrees"],
                ),
                fixture["target_dt"],
            )
        assert captured.value.reason in fixture["extreme_motion_allowed_reasons"]
        assert int(captured.value.evidence["required_substeps"]) > fixture[
            "maximum_internal_substeps"
        ]
        _assert_same_canonical_state(before, extreme.export_state())


def test_lbm_distinct_requested_intervals_execute_distinct_physical_updates(
    scenario_factory: ScenarioFactory,
) -> None:
    scenario = scenario_factory(resolution=(32, 16))
    geometry = NacaFoil(scenario.foil)
    shorter = LBMSolver()
    longer = LBMSolver()
    shorter.initialize(scenario, geometry, scenario.seed)
    longer.initialize(scenario, geometry, scenario.seed)

    shorter_report = shorter.advance(ControlState(0.0075, 4.0, 0.0), 0.0075)
    longer_report = longer.advance(ControlState(0.01, 4.0, 0.0), 0.01)

    assert shorter_report.advanced_dt == pytest.approx(0.0075)
    assert longer_report.advanced_dt == pytest.approx(0.01)
    assert not np.array_equal(
        shorter.export_state().velocity,
        longer.export_state().velocity,
    )


@pytest.mark.parametrize("solver_id", solver_ids())
def test_solver_accepts_runtime_reynolds_changes(
    solver_id: str,
    scenario_factory: ScenarioFactory,
) -> None:
    scenario = scenario_factory(resolution=(32, 16))
    solver = create_solver(solver_id)
    solver.initialize(scenario, NacaFoil(scenario.foil), scenario.seed)

    solver.set_reynolds(2.0 * scenario.reynolds)
    solver.advance(scenario.control_at(scenario.output_dt), scenario.output_dt)

    assert solver.reynolds == 2.0 * scenario.reynolds
    assert solver.diagnostics().values["requested_reynolds"] == 2.0 * scenario.reynolds
    assert np.isfinite(solver.export_state().velocity).all()
    with pytest.raises(ValueError, match="Reynolds"):
        solver.set_reynolds(0.0)


@pytest.mark.parametrize("solver_id", solver_ids())
def test_solver_is_deterministic_for_fixed_seed(
    solver_id: str, scenario_factory: ScenarioFactory
) -> None:
    scenario = scenario_factory(resolution=(32, 16))
    geometry = NacaFoil(scenario.foil)
    first = create_solver(solver_id)
    second = create_solver(solver_id)
    first.initialize(scenario, geometry, 17)
    second.initialize(scenario, geometry, 17)
    control = scenario.control_at(0.01)
    first.advance(control, 0.01)
    second.advance(control, 0.01)
    np.testing.assert_allclose(
        first.export_state().velocity,
        second.export_state().velocity,
        rtol=1.0e-6,
        atol=1.0e-6,
    )


def test_lbm_open_boundaries_remain_finite_for_many_steps(
    scenario_factory: ScenarioFactory,
) -> None:
    scenario = scenario_factory(
        resolution=(48, 24),
        foil_in_domain=False,
    )
    solver = create_solver("lbm-d2q9")
    solver.initialize(scenario, NacaFoil(scenario.foil), 0)
    report = None
    for step in range(300):
        time = (step + 1) * scenario.output_dt
        report = solver.advance(scenario.control_at(time), scenario.output_dt)

    assert report is not None
    assert any("effective Re" in warning for warning in report.warnings)
    state = solver.export_state()
    np.testing.assert_allclose(
        np.mean(state.velocity[0, :, :, 0]),
        scenario.freestream[0],
        rtol=0.01,
    )
    assert np.isfinite(state.velocity).all()


def test_pic_flip_abrupt_stall_does_not_amplify_transfer_error(
    scenario_factory: ScenarioFactory,
) -> None:
    scenario = scenario_factory(resolution=(160, 96))
    scenario = replace(
        scenario,
        domain=replace(
            scenario.domain,
            bounds=((-1.5, 3.5), (-1.5, 1.5)),
        ),
        reynolds=1000.0,
        output_dt=1.0 / 60.0,
        solver_options={
            **scenario.solver_options,
            "pressure_tolerance": 0.001,
        },
    )
    solver = create_solver("pic-flip")
    solver.initialize(scenario, NacaFoil(scenario.foil), scenario.seed)

    report = None
    for step in range(7):
        time = (step + 1) * scenario.output_dt
        report = solver.advance(ControlState(time, 25.0, 0.0), scenario.output_dt)

    assert report is not None
    diagnostics = solver.diagnostics()
    assert diagnostics.values["kinetic_energy"] < 1.0
    assert diagnostics.values["enstrophy"] < 100.0
    assert np.isfinite(solver.export_state().velocity).all()


def test_pic_flip_periodic_translation_preserves_population(
    scenario_factory: ScenarioFactory,
) -> None:
    scenario = scenario_factory(
        resolution=(40, 20),
        foil_in_domain=False,
        periodic_axes=("x", "y"),
    )
    scenario = replace(
        scenario,
        domain=replace(
            scenario.domain,
            bounds=((0.0, 1.0), (0.0, 0.5)),
        ),
        output_dt=1.0,
        solver_options={
            **scenario.solver_options,
            "pic_population_interval": 100,
        },
    )
    solver = create_solver("pic-flip")
    solver.initialize(scenario, NacaFoil(scenario.foil), scenario.seed)

    solver.advance(ControlState(1.0, 0.0, 0.0), 1.0)

    diagnostics = solver.diagnostics().values
    assert diagnostics["empty_fluid_cell_fraction"] == 0.0
    assert diagnostics["underfilled_fluid_cell_fraction"] == 0.0
    assert diagnostics["max_particles_per_fluid_cell"] == 4.0


def test_pic_flip_large_angle_change_resolves_pose_sweep_with_zero_wall_velocity(
    scenario_factory: ScenarioFactory,
) -> None:
    scenario = scenario_factory(resolution=(80, 40))
    scenario = replace(
        scenario,
        domain=replace(
            scenario.domain,
            bounds=((-1.5, 3.5), (-1.5, 1.5)),
        ),
        output_dt=1.0 / 60.0,
    )
    solver = create_solver("pic-flip")
    solver.initialize(scenario, NacaFoil(scenario.foil), scenario.seed)
    report = solver.advance(
        ControlState(scenario.output_dt, 30.0, 0.0),
        scenario.output_dt,
    )

    diagnostics = solver.diagnostics().values
    assert report.substeps > 1
    assert diagnostics["swept_collisions_last_step"] > 0.0
    assert diagnostics["particles_inside_solid"] == 0.0
    assert solver.export_state().angular_velocity_degrees == 0.0
    assert np.isfinite(solver.export_state().velocity).all()


def test_pic_flip_rejects_an_unsafe_configured_cfl(
    scenario_factory: ScenarioFactory,
) -> None:
    scenario = scenario_factory(resolution=(32, 16))
    scenario = replace(
        scenario,
        solver_options={**scenario.solver_options, "pic_cfl": 1.01},
    )
    solver = create_solver("pic-flip")

    with pytest.raises(ValueError, match="pic_cfl"):
        solver.initialize(scenario, NacaFoil(scenario.foil), scenario.seed)


def test_stable_fluids_honors_pressure_iteration_budget(
    scenario_factory: ScenarioFactory,
) -> None:
    scenario = scenario_factory(resolution=(64, 32))
    scenario = replace(
        scenario,
        solver_options={
            **scenario.solver_options,
            "pressure_max_iterations": 1,
            "pressure_tolerance": 1.0e-12,
        },
    )
    solver = StableFluidsSolver()

    with pytest.raises(NumericalFailure) as captured:
        solver.initialize(scenario, NacaFoil(scenario.foil), scenario.seed)

    assert captured.value.reason == "projection_failure"
    assert captured.value.stage == "projection"
    assert captured.value.evidence["iterations"] == 1


def test_pic_flip_pressure_exhaustion_rolls_back_private_state(
    scenario_factory: ScenarioFactory,
) -> None:
    scenario = scenario_factory(resolution=(64, 32))
    scenario = replace(
        scenario,
        reynolds=1.0e20,
        solver_options={
            **scenario.solver_options,
            "pressure_max_iterations": 1,
            "pressure_tolerance": 1.0e-12,
        },
    )
    solver = PicFlipSolver()
    solver.initialize(scenario, NacaFoil(scenario.foil), scenario.seed)
    before = solver.export_state()
    positions = solver._positions
    particle_velocity = solver._particle_velocity
    assert positions is not None and particle_velocity is not None
    positions_before = positions.copy()
    particle_velocity_before = particle_velocity.copy()
    rng_before = solver._rng.checkpoint()

    with pytest.raises(NumericalFailure) as captured:
        solver.advance(scenario.control_at(scenario.output_dt), scenario.output_dt)

    assert captured.value.reason == "projection_failure"
    assert captured.value.stage == "projection"
    assert captured.value.evidence["iterations"] == 1
    _assert_same_canonical_state(before, solver.export_state())
    np.testing.assert_array_equal(positions_before, solver._positions)
    np.testing.assert_array_equal(particle_velocity_before, solver._particle_velocity)
    assert solver._rng.checkpoint() == rng_before


def test_lbm_rejects_nonfinite_post_step_state(
    scenario_factory: ScenarioFactory,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    scenario = scenario_factory(resolution=(24, 12))
    solver = LBMSolver()
    solver.initialize(scenario, NacaFoil(scenario.foil), scenario.seed)
    before = solver.export_state()
    before_revision = solver.state_revision
    original_step = solver._step

    def nonfinite_step(control: ControlState) -> None:
        original_step(control)
        assert solver._f is not None
        solver._f[0, 0, 0] = np.nan

    monkeypatch.setattr(solver, "_step", nonfinite_step)
    with pytest.raises(NumericalFailure, match="populations") as captured:
        solver.advance(scenario.control_at(scenario.output_dt), scenario.output_dt)

    assert captured.value.reason == "invalid_population"
    assert captured.value.stage == "postcondition"
    assert solver.state_revision == before_revision
    _assert_same_canonical_state(before, solver.export_state())


def test_pic_flip_rejects_nonfinite_post_step_state(
    scenario_factory: ScenarioFactory,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    scenario = scenario_factory(resolution=(24, 12))
    solver = PicFlipSolver()
    solver.initialize(scenario, NacaFoil(scenario.foil), scenario.seed)
    before = solver.export_state()
    before_revision = solver.state_revision

    def nonfinite_projection(
        u: np.ndarray,
        v: np.ndarray,
        control: ControlState,
        dt: float,
    ) -> tuple[np.ndarray, np.ndarray]:
        del control, dt
        return np.full_like(u, np.nan), np.full_like(v, np.nan)

    monkeypatch.setattr(solver, "_project_faces", nonfinite_projection)
    with pytest.raises(NumericalFailure, match="non-finite state") as captured:
        solver.advance(scenario.control_at(scenario.output_dt), scenario.output_dt)

    assert captured.value.reason == "nonfinite_state"
    assert solver.state_revision == before_revision
    _assert_same_canonical_state(before, solver.export_state())


def test_stable_fluids_failed_advance_rolls_back_all_canonical_state(
    scenario_factory: ScenarioFactory,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    scenario = scenario_factory(resolution=(24, 12))
    solver = StableFluidsSolver()
    solver.initialize(scenario, NacaFoil(scenario.foil), scenario.seed)
    before = solver.export_state()
    before_revision = solver.state_revision

    def failed_projection(_dt: float) -> None:
        assert solver._u is not None
        solver._u[0, 0] = 123.0
        raise NumericalFailure("projection_failure", "injected failure", "projection")

    monkeypatch.setattr(solver, "_apply_projection", failed_projection)
    with pytest.raises(NumericalFailure, match="injected failure"):
        solver.advance(scenario.control_at(scenario.output_dt), scenario.output_dt)

    assert solver.state_revision == before_revision
    _assert_same_canonical_state(before, solver.export_state())


@pytest.mark.parametrize("solver_id", solver_ids())
def test_rejected_warm_import_is_transactional(
    solver_id: str,
    scenario_factory: ScenarioFactory,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    scenario = scenario_factory(resolution=(24, 12))
    solver = create_solver(solver_id)
    solver.initialize(scenario, NacaFoil(scenario.foil), scenario.seed)
    before = solver.export_state()
    before_revision = solver.state_revision
    imported = replace(
        before,
        time=0.25,
        angle_degrees=12.0,
        angular_velocity_degrees=0.0,
    )
    control = ControlState(0.25, 12.0, 0.0)

    def fail(*_arguments: object) -> None:
        raise NumericalFailure(
            "projection_failure",
            "injected warm-import failure",
            "canonical-import",
        )

    if solver_id == "stable-fluids":
        monkeypatch.setattr(solver, "_apply_projection", fail)
    elif solver_id == "lbm-d2q9":
        monkeypatch.setattr(solver, "_equilibrium", fail)
    else:
        monkeypatch.setattr(solver, "_seed_particles", fail)

    outcome = solver.import_state(imported, control)

    assert not outcome.accepted
    assert outcome.reason == "projection_failure"
    assert outcome.stage == "canonical-import"
    assert solver.state_revision == before_revision
    _assert_same_canonical_state(before, solver.export_state())
