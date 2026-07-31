"""Semi-Lagrangian Stable Fluids reference on a staggered MAC grid."""

import numpy as np

from foilbench_py.core.geometry import NacaFoil, cell_centers
from foilbench_py.core.grid import (
    advect_velocity,
    cell_to_faces,
    faces_to_cell,
    implicit_diffuse,
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
    ImportReport,
    Scenario,
    SolverInfo,
    StepReport,
)
from foilbench_py.types import FaceVelocityX, FaceVelocityY, MaskField, PointCloud, VelocityField


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

    def initialize(self, scenario: Scenario, geometry: NacaFoil, seed: int) -> None:
        del seed
        if scenario.domain.dimension != 2:
            raise NotImplementedError("Stable Fluids Phase 1 supports only 2D")
        self._scenario = scenario
        self._geometry = geometry
        self._control = scenario.control_at(0.0)
        self._time = 0.0
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
        self._maccormack = (
            str(scenario.solver_options.get("stable_advection", "maccormack")) == "maccormack"
        )
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
        self._u, self._v, info = project_faces(
            u,
            v,
            scenario.domain,
            solid,
            self._wall_grid(self._control),
            scenario.freestream,
            dt,
            channel,
            pressure_tolerance,
        )
        self._projection_warning = "" if info == 0 else f"pressure CG returned {info}"

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
        stable_dt = cfl * min(scenario.domain.dx, scenario.domain.dy) / max_speed
        substeps = max(1, int(np.ceil(target_dt / stable_dt)))
        dt = target_dt / substeps
        viscosity = scenario.reference_speed * scenario.foil.chord / scenario.reynolds
        for substep in range(substeps):
            fraction = (substep + 1) / substeps
            sub_control = ControlState(
                self._time + fraction * target_dt,
                self._control.angle_degrees
                + fraction * (control.angle_degrees - self._control.angle_degrees),
                control.angular_velocity_degrees,
            )
            _, _, current_u, current_v, _ = self._require()
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

    def import_state(self, state: CanonicalFlowState, control: ControlState) -> ImportReport:
        scenario, geometry, _, _, _ = self._require()
        if state.dimension != 2 or state.resolution != scenario.domain.resolution:
            raise ValueError("warm import requires the same 2D resolution")
        velocity = np.asarray(state.velocity[0], dtype=scenario.dtype)
        self._u, self._v = cell_to_faces(velocity)
        self._time = state.time
        self._control = control
        self._solid = geometry.mask(scenario.domain, control.angle_degrees)
        self._apply_projection(max(scenario.output_dt, 1.0e-4))
        return ImportReport(
            state.source_solver,
            self.info.id,
            ("pressure", "face-centered projection history"),
            () if not self._projection_warning else (self._projection_warning,),
        )

    def diagnostics(self) -> Diagnostics:
        scenario, _, _, _, solid = self._require()
        velocity = self.cell_velocity()
        values = {
            "time": self._time,
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
            raise FloatingPointError("Stable Fluids produced non-finite diagnostics")
        return Diagnostics(values, warnings)
