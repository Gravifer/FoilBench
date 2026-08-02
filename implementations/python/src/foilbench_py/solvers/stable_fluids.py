"""Semi-Lagrangian Stable Fluids reference on a staggered MAC grid."""

from typing import Literal

import numpy as np

from foilbench_py.core.geometry import NacaFoil, cell_centers
from foilbench_py.core.grid import (
    advect_faces,
    advect_faces_skew_rk2,
    advect_velocity,
    cell_to_faces,
    faces_to_cell,
    implicit_diffuse,
    implicit_diffuse_faces,
    project_faces,
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
    NumericalFailure,
    Scenario,
    SolverInfo,
    StepReport,
)
from foilbench_py.types import FaceVelocityX, FaceVelocityY, MaskField, PointCloud, VelocityField

_MIN_PROJECTION_CFL_LIMIT = 1.0
type StableTransportMode = Literal["maccormack", "semi-lagrangian", "skew-rk2"]


def parse_stable_transport_mode(value: object) -> StableTransportMode:
    if value == "maccormack":
        return "maccormack"
    if value == "semi-lagrangian":
        return "semi-lagrangian"
    if value == "skew-rk2":
        return "skew-rk2"
    raise ValueError(f"unsupported Stable Fluids advection: {value}")


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
        self._control = ControlState(0.0, 0.0, 0.0)
        self._time = 0.0
        self._projection_warning = ""
        self._maccormack = True
        self._face_advection = False
        self._skew_rk2 = False
        self._reynolds = 1.0

    @property
    def reynolds(self) -> float:
        return self._reynolds

    def set_reynolds(self, reynolds: float) -> None:
        if not np.isfinite(reynolds) or reynolds <= 0.0:
            raise ValueError("Reynolds number must be finite and positive")
        self._reynolds = float(reynolds)

    @property
    def transport_mode(self) -> StableTransportMode:
        if self._skew_rk2:
            return "skew-rk2"
        return "maccormack" if self._maccormack else "semi-lagrangian"

    def set_transport_mode(self, mode: StableTransportMode) -> None:
        self._maccormack = mode == "maccormack"
        self._skew_rk2 = mode == "skew-rk2"

    def initialize(self, scenario: Scenario, geometry: NacaFoil, seed: int) -> None:
        del seed
        if scenario.domain.dimension != 2:
            raise NotImplementedError("Stable Fluids Phase 1 supports only 2D")
        self._scenario = scenario
        self._geometry = geometry
        self._control = scenario.control_at(0.0)
        self._time = 0.0
        self.set_reynolds(scenario.reynolds)
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
        tolerance_option = scenario.solver_options.get("pressure_tolerance", 1.0e-5)
        if not isinstance(tolerance_option, (int, float)):
            raise TypeError("pressure_tolerance must be numeric")
        pressure_tolerance = float(tolerance_option)
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
            )
        self._u, self._v, info = project_faces(
            u,
            v,
            scenario.domain,
            solid,
            wall_velocity,
            scenario.freestream,
            dt,
            channel,
            pressure_tolerance,
        )
        if info != 0:
            raise NumericalFailure(
                "projection_failure",
                f"Stable Fluids pressure CG did not converge: {info}",
            )
        if not np.isfinite(self._u).all() or not np.isfinite(self._v).all():
            raise NumericalFailure(
                "nonfinite_state",
                "Stable Fluids projection produced non-finite velocity",
            )
        self._projection_warning = ""

    def advance(self, control: ControlState, target_dt: float) -> StepReport:
        scenario, geometry, _, _, _ = self._require()
        if target_dt <= 0.0:
            raise ValueError("target_dt must be positive")
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
        stable_dt = cfl * min(scenario.domain.dx, scenario.domain.dy) / max_speed
        substeps = max(1, int(np.ceil(target_dt / stable_dt)))
        dt = target_dt / substeps
        viscosity = scenario.reference_speed * scenario.foil.chord / self._reynolds
        for substep in range(substeps):
            fraction = (substep + 1) / substeps
            sub_control = ControlState(
                self._time + fraction * target_dt,
                self._control.angle_degrees
                + fraction * (control.angle_degrees - self._control.angle_degrees),
                control.angular_velocity_degrees,
            )
            _, _, current_u, current_v, current_solid = self._require()
            if self._skew_rk2:
                self._u, self._v = advect_faces_skew_rk2(
                    current_u,
                    current_v,
                    dt,
                    scenario.domain,
                    current_solid,
                    self._wall_grid(sub_control),
                    scenario.freestream,
                )
                self._u, self._v = implicit_diffuse_faces(
                    self._u, self._v, viscosity, dt, scenario.domain
                )
            elif self._face_advection:
                advected_u, advected_v = advect_faces(
                    current_u,
                    current_v,
                    dt,
                    scenario.domain,
                    self._maccormack,
                )
                self._u, self._v = implicit_diffuse_faces(
                    advected_u, advected_v, viscosity, dt, scenario.domain
                )
            else:
                velocity = faces_to_cell(current_u, current_v)
                velocity = advect_velocity(velocity, dt, scenario.domain, self._maccormack)
                velocity = implicit_diffuse(velocity, viscosity, dt, scenario.domain)
                self._u, self._v = cell_to_faces(velocity)
            self._control = sub_control
            self._solid = geometry.mask(scenario.domain, sub_control.angle_degrees)
            self._apply_projection(dt)
        self._time += target_dt
        self._control = ControlState(
            self._time, control.angle_degrees, control.angular_velocity_degrees
        )
        warnings = () if not self._projection_warning else (self._projection_warning,)
        return StepReport(target_dt, target_dt, substeps, max_speed, warnings)

    def cell_velocity(self) -> VelocityField:
        _, _, u, v, _ = self._require()
        return faces_to_cell(u, v)

    def sample_velocity(self, points: PointCloud) -> PointCloud:
        scenario, _, _, _, _ = self._require()
        return sample_vector(self.cell_velocity(), points, scenario.domain)

    def export_state(self) -> CanonicalFlowState:
        scenario, _, _, _, _ = self._require()
        velocity = self.cell_velocity()[None, ...]
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
        scenario, geometry, _, _, _ = self._require()
        if state.dimension != 2 or state.resolution != scenario.domain.resolution:
            return ImportOutcome(
                "rejected",
                "incompatible_domain",
                warnings=("warm import requires the same 2D resolution",),
            )
        velocity = np.asarray(state.velocity[0], dtype=scenario.dtype)
        self._u, self._v = cell_to_faces(velocity)
        self._time = state.time
        self._control = control
        self._solid = geometry.mask(scenario.domain, control.angle_degrees)
        try:
            self._apply_projection(max(scenario.output_dt, 1.0e-4))
        except NumericalFailure as failure:
            return ImportOutcome("rejected", failure.reason, warnings=(str(failure),))
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
        return Diagnostics(values, warnings)
