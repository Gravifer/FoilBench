"""Semi-Lagrangian Stable Fluids reference on a staggered MAC grid."""

from typing import Literal

import numpy as np

from foilbench_py.core.geometry import NacaFoil, cell_centers
from foilbench_py.core.grid import (
    IterativeReport,
    advect_faces,
    advect_faces_skew_rk2,
    advect_velocity,
    cell_to_faces,
    faces_to_cell,
    implicit_diffuse,
    implicit_diffuse_faces,
    native_divergence_linf,
    project_faces,
    solid_face_leakage,
)
from foilbench_py.core.interpolation import sample_vector
from foilbench_py.core.metrics import (
    divergence_l2,
    enstrophy,
    kinetic_energy,
    recirculation_area,
    solid_leakage,
    wake_width,
)
from foilbench_py.core.models import (
    CanonicalFlowState,
    ControlState,
    Diagnostics,
    ImportOutcome,
    ImportReport,
    InteractiveTuning,
    NumericalFailure,
    RestartState,
    ReynoldsOutcome,
    Scenario,
    SolverInfo,
    StepReport,
)
from foilbench_py.core.solver_validation import (
    validate_advance_request,
    validate_canonical_import,
    validate_restart_state,
)
from foilbench_py.types import FaceVelocityX, FaceVelocityY, MaskField, PointCloud, VelocityField

_MIN_PROJECTION_CFL_LIMIT = 1.0
type StableTransportMode = Literal["maccormack", "semi-lagrangian", "skew-rk2"]
type StableCheckpoint = tuple[
    FaceVelocityX,
    FaceVelocityY,
    MaskField,
    ControlState,
    float,
    str,
    int,
    IterativeReport,
    IterativeReport,
]


def parse_stable_transport_mode(value: object) -> StableTransportMode:
    if value == "maccormack":
        return "maccormack"
    if value == "semi-lagrangian":
        return "semi-lagrangian"
    if value == "skew-rk2":
        return "skew-rk2"
    raise ValueError(f"unsupported Stable Fluids advection: {value}")


def _float_option(scenario: Scenario, name: str, default: float) -> float:
    value = scenario.solver_options.get(name, default)
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise TypeError(f"{name} must be numeric")
    return float(value)


def _int_option(scenario: Scenario, name: str, default: int) -> int:
    value = scenario.solver_options.get(name, default)
    if isinstance(value, bool) or not isinstance(value, int):
        raise TypeError(f"{name} must be an integer")
    return value


class StableFluidsSolver:
    info = SolverInfo(
        id="stable-fluids",
        display_name="Stable Fluids (MAC)",
        dimensions=(2,),
        supports_moving_boundary=True,
        acceleration="NumPy + SciPy matrix-free CG",
    )

    def __init__(self) -> None:
        self._scenario: Scenario | None = None
        self._geometry: NacaFoil | None = None
        self._u: FaceVelocityX | None = None
        self._v: FaceVelocityY | None = None
        self._solid: MaskField | None = None
        self._control: ControlState = ControlState(0.0, 0.0, 0.0)
        self._time: float = 0.0
        self._projection_warning: str = ""
        self._last_projection: IterativeReport = IterativeReport(
            "relative-l2", 0.0, 0, 0.0, True
        )
        self._last_viscosity: IterativeReport = IterativeReport(
            "update-linf", 0.0, 0, 0.0, True
        )
        self._maccormack: bool = True
        self._face_advection: bool = False
        self._skew_rk2: bool = False
        self._reynolds: float = 1.0
        self._revision: int = 0

    @property
    def reynolds(self) -> float:
        return self._reynolds

    @property
    def state_revision(self) -> int:
        return self._revision

    def set_reynolds(self, reynolds: float) -> ReynoldsOutcome:
        if not np.isfinite(reynolds) or reynolds <= 0.0:
            raise ValueError("Reynolds number must be finite and positive")
        selected = float(reynolds)
        if selected != self._reynolds:
            self._revision += 1
        self._reynolds = selected
        return ReynoldsOutcome(self._reynolds, self._reynolds)

    @property
    def transport_mode(self) -> StableTransportMode:
        if self._skew_rk2:
            return "skew-rk2"
        return "maccormack" if self._maccormack else "semi-lagrangian"

    def set_transport_mode(self, mode: StableTransportMode) -> None:
        changed = mode != self.transport_mode
        self._maccormack = mode == "maccormack"
        self._skew_rk2 = mode == "skew-rk2"
        if changed and self._scenario is not None:
            self._revision += 1

    def interactive_tuning(self) -> InteractiveTuning:
        mode = self.transport_mode
        return InteractiveTuning(
            "stable-advection",
            "adv",
            mode,
            mode,
            mode != "maccormack",
            mode != "skew-rk2",
        )

    def adjust_interactive_tuning(self, direction: int) -> InteractiveTuning:
        self.set_transport_mode("maccormack" if direction < 0 else "skew-rk2")
        return self.interactive_tuning()

    def apply_interactive_tuning(self, value: str | float) -> InteractiveTuning:
        self.set_transport_mode(parse_stable_transport_mode(value))
        return self.interactive_tuning()

    def initialize(self, scenario: Scenario, geometry: NacaFoil, seed: int) -> None:
        del seed
        if scenario.domain.dimension != 2:
            raise NotImplementedError("Stable Fluids Phase 1 supports only 2D")
        self._scenario = scenario
        self._geometry = geometry
        self._control = scenario.control_at(0.0)
        self._time = 0.0
        self._revision = 0
        self._reynolds = float(scenario.reynolds)
        self._last_projection = IterativeReport("relative-l2", 0.0, 0, 0.0, True)
        self._last_viscosity = IterativeReport("update-linf", 0.0, 0, 0.0, True)
        velocity = np.empty((scenario.domain.ny, scenario.domain.nx, 2), dtype=scenario.dtype)
        velocity[...] = np.asarray(scenario.freestream[:2], dtype=scenario.dtype)
        initial = str(scenario.solver_options.get("initial_condition", "freestream"))
        positions = cell_centers(scenario.domain)
        if initial == "taylor-green":
            velocity[:, :, 0] = np.sin(positions[:, :, 0]) * np.cos(positions[:, :, 1])
            velocity[:, :, 1] = -np.cos(positions[:, :, 0]) * np.sin(positions[:, :, 1])
        elif initial == "poiseuille":
            y0, y1 = scenario.domain.bounds[1]
            radius = 0.5 * (y1 - y0)
            center = 0.5 * (y0 + y1)
            velocity[:, :, 0] = 1.5 * (1.0 - ((positions[:, :, 1] - center) / radius) ** 2)
            velocity[:, :, 1] = 0.0
        self._u, self._v = cell_to_faces(velocity)
        self._solid = geometry.mask(scenario.domain, self._control.angle_degrees)
        advection = parse_stable_transport_mode(
            scenario.solver_options.get("stable_advection", "maccormack")
        )
        self.set_transport_mode(advection)
        self._face_advection = bool(scenario.solver_options.get("stable_face_advection", False))
        self._apply_projection(max(scenario.output_dt, 1.0e-4))
        self._revision = 0

    def restart(
        self,
        scenario: Scenario,
        geometry: NacaFoil,
        seed: int,
        start: RestartState,
    ) -> None:
        validate_restart_state(start)
        self.initialize(scenario, geometry, seed)
        self.set_reynolds(start.reynolds)
        self._time = start.time
        self._control = ControlState(start.time, start.angle_degrees, 0.0)
        self._solid = geometry.mask(scenario.domain, start.angle_degrees)
        velocity = np.empty(
            (scenario.domain.ny, scenario.domain.nx, 2),
            dtype=scenario.dtype,
        )
        velocity[...] = np.asarray(scenario.freestream[:2], dtype=scenario.dtype)
        wall = self._wall_grid(self._control)
        velocity[self._solid] = wall[self._solid]
        self._u, self._v = cell_to_faces(velocity)
        self._apply_projection(max(scenario.output_dt, 1.0e-4))
        self._revision = 0

    def _require(self) -> tuple[Scenario, NacaFoil, FaceVelocityX, FaceVelocityY, MaskField]:
        if (
            self._scenario is None
            or self._geometry is None
            or self._u is None
            or self._v is None
            or self._solid is None
        ):
            raise RuntimeError("solver has not been initialized")
        return self._scenario, self._geometry, self._u, self._v, self._solid

    def _wall_grid(self, control: ControlState) -> VelocityField:
        scenario, geometry, _, _, _ = self._require()
        points = cell_centers(scenario.domain).reshape(-1, 2)
        return geometry.wall_velocity(points, control).reshape(
            scenario.domain.ny, scenario.domain.nx, 2
        )

    def _apply_projection(self, dt: float) -> None:
        scenario, _, u, v, solid = self._require()
        channel = str(scenario.solver_options.get("initial_condition", "")) == "poiseuille"
        pressure_tolerance = _float_option(scenario, "pressure_tolerance", 1.0e-5)
        pressure_max_iterations = _int_option(
            scenario, "pressure_max_iterations", 640
        )
        cfl_option = scenario.solver_options.get("stable_cfl", 0.7)
        if not isinstance(cfl_option, (int, float)):
            raise TypeError("stable_cfl must be numeric")
        configured_cfl = float(cfl_option)
        if self._skew_rk2:
            configured_cfl = min(configured_cfl, 0.4)
        projection_cfl_limit = max(_MIN_PROJECTION_CFL_LIMIT, 2.0 * configured_cfl)
        wall_velocity = self._wall_grid(self._control)
        arrays = (u, v, wall_velocity)
        if not all(np.isfinite(array).all() for array in arrays):
            raise NumericalFailure(
                "nonfinite_state",
                "Stable Fluids projection received non-finite velocity",
                "projection",
            )
        face_speed = max(float(np.max(np.abs(u))), float(np.max(np.abs(v))))
        wall_speed = (
            float(np.max(np.linalg.norm(wall_velocity[solid], axis=1)))
            if np.any(solid)
            else 0.0
        )
        projection_cfl = max(face_speed, wall_speed) * dt / min(
            scenario.domain.dx,
            scenario.domain.dy,
        )
        if projection_cfl > projection_cfl_limit:
            raise NumericalFailure(
                "excessive_velocity",
                f"Stable Fluids projection CFL {projection_cfl:.2f} exceeds "
                f"{projection_cfl_limit:.2f}",
                "projection",
                {
                    "maximum_cfl": projection_cfl,
                    "maximum_cfl_limit": projection_cfl_limit,
                },
            )
        self._u, self._v, report = project_faces(
            u,
            v,
            scenario.domain,
            solid,
            wall_velocity,
            scenario.freestream,
            dt,
            channel,
            pressure_tolerance,
            pressure_max_iterations,
        )
        self._last_projection = report
        if not report.converged:
            raise NumericalFailure(
                "projection_failure",
                "Stable Fluids pressure CG did not converge",
                "projection",
                {
                    "criterion": report.criterion,
                    "iterations": report.iterations,
                    "tolerance": report.tolerance,
                    "relative_residual": report.final_residual,
                },
            )
        if not np.isfinite(self._u).all() or not np.isfinite(self._v).all():
            raise NumericalFailure(
                "nonfinite_state",
                "Stable Fluids projection produced non-finite velocity",
                "projection",
            )
        self._projection_warning = ""

    def advance(self, control: ControlState, target_dt: float) -> StepReport:
        scenario, geometry, current_u, current_v, current_solid = self._require()
        validate_advance_request(self._time, control, target_dt, scenario.precision)
        cell_velocity = self.cell_velocity()
        max_speed = max(
            float(np.max(np.linalg.norm(cell_velocity, axis=2))),
            abs(scenario.freestream[0]),
            1.0e-6,
        )
        cfl_option = scenario.solver_options.get("stable_cfl", 0.7)
        if not isinstance(cfl_option, (int, float)):
            raise TypeError("stable_cfl must be numeric")
        cfl = float(cfl_option)
        if self._skew_rk2:
            cfl = min(cfl, 0.4)
        spacing = min(scenario.domain.dx, scenario.domain.dy)
        wall_speed = abs(np.deg2rad(control.angular_velocity_degrees)) * geometry.maximum_radius
        sweep_cells = (
            abs(np.deg2rad(control.angle_degrees - self._control.angle_degrees))
            * geometry.maximum_radius
            / spacing
        )
        fluid_measure = (
            target_dt * max_speed * (1.0 / scenario.domain.dx + 1.0 / scenario.domain.dy)
            if self._skew_rk2
            else target_dt * max_speed / spacing
        )
        required = max(
            1.05 * fluid_measure / cfl,
            target_dt * wall_speed / (cfl * spacing),
            sweep_cells / cfl,
        )
        substeps = max(1, int(np.ceil(required)))
        if substeps > 512:
            raise NumericalFailure(
                "stability_limit",
                "Stable Fluids motion requires too many internal substeps",
                "advection" if self._skew_rk2 else "boundary",
                {
                    "required_substeps": substeps,
                    "maximum_substeps": 512,
                    "maximum_fluid_speed": max_speed,
                    "maximum_wall_speed": wall_speed,
                    "boundary_sweep_cells": sweep_cells,
                },
            )
        dt = target_dt / substeps
        viscosity = scenario.reference_speed * scenario.foil.chord / self._reynolds
        checkpoint: StableCheckpoint = (
            current_u.copy(),
            current_v.copy(),
            current_solid.copy(),
            self._control,
            self._time,
            self._projection_warning,
            self._revision,
            self._last_projection,
            self._last_viscosity,
        )
        start_time = self._time
        start_angle = self._control.angle_degrees
        try:
            for substep in range(substeps):
                fraction = (substep + 1) / substeps
                sub_control = ControlState(
                    start_time + fraction * target_dt,
                    start_angle + fraction * (control.angle_degrees - start_angle),
                    control.angular_velocity_degrees,
                )
                _, _, step_u, step_v, step_solid = self._require()
                if self._skew_rk2:
                    self._u, self._v = advect_faces_skew_rk2(
                        step_u,
                        step_v,
                        dt,
                        scenario.domain,
                        step_solid,
                        self._wall_grid(sub_control),
                        scenario.freestream,
                    )
                    self._u, self._v, self._last_viscosity = implicit_diffuse_faces(
                        self._u,
                        self._v,
                        viscosity,
                        dt,
                        scenario.domain,
                        _float_option(scenario, "pressure_tolerance", 1.0e-5),
                        _int_option(scenario, "pressure_max_iterations", 640),
                    )
                elif self._face_advection:
                    advected_u, advected_v = advect_faces(
                        step_u,
                        step_v,
                        dt,
                        scenario.domain,
                        self._maccormack,
                    )
                    self._u, self._v, self._last_viscosity = implicit_diffuse_faces(
                        advected_u,
                        advected_v,
                        viscosity,
                        dt,
                        scenario.domain,
                        _float_option(scenario, "pressure_tolerance", 1.0e-5),
                        _int_option(scenario, "pressure_max_iterations", 640),
                    )
                else:
                    velocity = faces_to_cell(step_u, step_v)
                    velocity = advect_velocity(
                        velocity, dt, scenario.domain, self._maccormack
                    )
                    velocity, self._last_viscosity = implicit_diffuse(
                        velocity,
                        viscosity,
                        dt,
                        scenario.domain,
                        _float_option(scenario, "pressure_tolerance", 1.0e-5),
                        _int_option(scenario, "pressure_max_iterations", 640),
                    )
                    self._u, self._v = cell_to_faces(velocity)
                if not self._last_viscosity.converged:
                    raise NumericalFailure(
                        "convergence_failure",
                        "Stable Fluids implicit viscosity did not converge",
                        "viscosity",
                        {
                            "criterion": self._last_viscosity.criterion,
                            "iterations": self._last_viscosity.iterations,
                            "tolerance": self._last_viscosity.tolerance,
                            "final_residual": self._last_viscosity.final_residual,
                        },
                    )
                self._control = sub_control
                self._solid = geometry.mask(scenario.domain, sub_control.angle_degrees)
                self._apply_projection(dt)
            final_velocity = self.cell_velocity()
            if not np.isfinite(final_velocity).all():
                raise NumericalFailure(
                    "nonfinite_state",
                    "Stable Fluids produced non-finite velocity",
                    "postcondition",
                )
            final_speed = float(np.max(np.linalg.norm(final_velocity, axis=2)))
            accepted_measure = (
                dt * final_speed * (1.0 / scenario.domain.dx + 1.0 / scenario.domain.dy)
                if self._skew_rk2
                else dt * final_speed / spacing
            )
            if accepted_measure > cfl * (1.0 + 1.0e-6):
                raise NumericalFailure(
                    "stability_limit",
                    "Stable Fluids post-step motion exceeded its transport envelope",
                    "advection",
                    {
                        "accepted_measure": accepted_measure,
                        "maximum_measure": cfl,
                    },
                )
        except Exception:
            (
                self._u,
                self._v,
                self._solid,
                self._control,
                self._time,
                self._projection_warning,
                self._revision,
                self._last_projection,
                self._last_viscosity,
            ) = checkpoint
            raise
        self._time = start_time + target_dt
        self._control = ControlState(
            self._time,
            control.angle_degrees,
            control.angular_velocity_degrees,
        )
        self._revision += 1
        warnings = () if not self._projection_warning else (self._projection_warning,)
        _, _, final_u, final_v, final_solid = self._require()
        final_wall = self._wall_grid(self._control)
        native_divergence = native_divergence_linf(
            final_u,
            final_v,
            scenario.domain,
            final_solid,
        )
        native_leakage = solid_face_leakage(
            final_u,
            final_v,
            final_solid,
            final_wall,
        )
        return StepReport(
            target_dt,
            target_dt,
            substeps,
            final_speed,
            warnings,
            self._revision,
            {
                "maximum_fluid_speed": final_speed,
                "maximum_wall_speed": wall_speed,
                "maximum_characteristic_displacement": accepted_measure,
                "maximum_boundary_sweep": sweep_cells / substeps,
                "pressure_converged": True,
                "pressure_iterations": self._last_projection.iterations,
                "pressure_relative_residual": self._last_projection.final_residual,
                "viscosity_converged": self._last_viscosity.converged,
                "viscosity_iterations": self._last_viscosity.iterations,
                "viscosity_final_residual": self._last_viscosity.final_residual,
                "divergence_linf": native_divergence,
                "solid_leakage": native_leakage,
                "requested_reynolds": self._reynolds,
                "effective_reynolds": self._reynolds,
                "degraded_motion": wall_speed == 0.0
                and abs(control.angle_degrees - start_angle) > 1.0e-9,
            },
        )

    def cell_velocity(self) -> VelocityField:
        _, _, u, v, _ = self._require()
        return faces_to_cell(u, v)

    def sample_velocity(self, points: PointCloud) -> PointCloud:
        scenario, _, _, _, _ = self._require()
        return sample_vector(self.cell_velocity(), points, scenario.domain)

    def export_state(self) -> CanonicalFlowState:
        scenario, _, _, _, _ = self._require()
        velocity_2d = self.cell_velocity().copy()
        _, _, _, _, solid = self._require()
        velocity_2d[solid] = 0.0
        velocity = velocity_2d[None, ...]
        return CanonicalFlowState(
            schema_version=1,
            dimension=2,
            bounds=scenario.domain.bounds,
            resolution=scenario.domain.resolution,
            periodic_axes=scenario.domain.periodic_axes,
            time=self._time,
            precision=scenario.precision,
            angle_degrees=self._control.angle_degrees,
            angular_velocity_degrees=self._control.angular_velocity_degrees,
            source_language="python",
            source_solver=self.info.id,
            velocity=velocity,
        )

    def import_state(self, state: CanonicalFlowState, control: ControlState) -> ImportOutcome:
        scenario, geometry, u, v, solid = self._require()
        checkpoint: StableCheckpoint = (
            u.copy(),
            v.copy(),
            solid.copy(),
            self._control,
            self._time,
            self._projection_warning,
            self._revision,
            self._last_projection,
            self._last_viscosity,
        )
        try:
            validate_canonical_import(state, scenario, control)
            velocity = np.asarray(state.velocity[0], dtype=scenario.dtype).copy()
            self._time = state.time
            self._control = control
            self._solid = geometry.mask(scenario.domain, control.angle_degrees)
            wall = self._wall_grid(control)
            velocity[self._solid] = wall[self._solid]
            self._u, self._v = cell_to_faces(velocity)
            self._apply_projection(max(scenario.output_dt, 1.0e-4))
        except NumericalFailure as failure:
            (
                self._u,
                self._v,
                self._solid,
                self._control,
                self._time,
                self._projection_warning,
                self._revision,
                self._last_projection,
                self._last_viscosity,
            ) = checkpoint
            return ImportOutcome(
                "rejected",
                failure.reason,
                warnings=(str(failure),),
                stage=failure.stage,
                evidence=failure.evidence,
            )
        except Exception:
            (
                self._u,
                self._v,
                self._solid,
                self._control,
                self._time,
                self._projection_warning,
                self._revision,
                self._last_projection,
                self._last_viscosity,
            ) = checkpoint
            raise
        self._revision += 1
        report = ImportReport(
            state.source_solver,
            self.info.id,
            ("pressure", "face-centered projection history"),
            (
                "Stable Fluids rebuilt pressure and face-projection history.",
                *((self._projection_warning,) if self._projection_warning else ()),
            ),
        )
        return ImportOutcome("accepted", "none", report, report.warnings)

    def diagnostics(self) -> Diagnostics:
        scenario, _, _, _, solid = self._require()
        velocity = self.cell_velocity()
        values = {
            "time": self._time,
            "requested_reynolds": self._reynolds,
            "kinetic_energy": kinetic_energy(velocity),
            "enstrophy": enstrophy(velocity, scenario.domain),
            "divergence_l2": divergence_l2(velocity, scenario.domain),
            "solid_leakage": solid_leakage(velocity, solid),
            "wake_width": wake_width(velocity, scenario.domain, scenario.foil.pivot[0]),
            "recirculation_area": recirculation_area(
                velocity, scenario.domain, scenario.foil.pivot[0]
            ),
        }
        warnings = () if not self._projection_warning else (self._projection_warning,)
        if not all(np.isfinite(value) for value in values.values()):
            raise NumericalFailure(
                "nonfinite_state",
                "Stable Fluids produced non-finite diagnostics",
            )
        return Diagnostics(values, warnings, self._revision)
