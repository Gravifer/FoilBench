"""Blended PIC/FLIP reference with quadratic B-spline transfers."""

import numpy as np

from foilbench_py.core.geometry import NacaFoil, cell_centers
from foilbench_py.core.grid import (
    IterativeReport,
    cell_to_faces,
    faces_to_cell,
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
from foilbench_py.core.particle_population import (
    maintain_particle_population,
    particle_cell_counts,
)
from foilbench_py.core.rng import PCG32
from foilbench_py.core.solver_validation import (
    validate_advance_request,
    validate_canonical_import,
    validate_restart_state,
)
from foilbench_py.solvers._numba_adapter import faces_to_particle, particle_to_faces
from foilbench_py.types import (
    CoordinateField,
    FaceVelocityX,
    FaceVelocityY,
    MaskField,
    ParticleVelocity,
    PointCloud,
    VelocityField,
)

type PicAdvanceCheckpoint = tuple[
    PointCloud,
    ParticleVelocity,
    VelocityField,
    MaskField,
    float | None,
    ControlState,
    float,
    tuple[int, int],
    str,
    IterativeReport,
    IterativeReport,
    int,
    int,
    int,
    int,
    int,
    float,
]
type PicImportCheckpoint = tuple[
    PointCloud,
    ParticleVelocity,
    VelocityField,
    MaskField,
    float | None,
    ControlState,
    float,
    tuple[int, int],
    int,
    int,
    float,
]


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


def _initial_velocity(scenario: Scenario) -> VelocityField:
    velocity = np.empty((scenario.domain.ny, scenario.domain.nx, 2), dtype=scenario.dtype)
    velocity[...] = np.asarray(scenario.freestream[:2], dtype=scenario.dtype)
    initial = str(scenario.solver_options.get("initial_condition", "freestream"))
    centers = cell_centers(scenario.domain)
    if initial == "taylor-green":
        velocity[:, :, 0] = np.sin(centers[:, :, 0]) * np.cos(centers[:, :, 1])
        velocity[:, :, 1] = -np.cos(centers[:, :, 0]) * np.sin(centers[:, :, 1])
    elif initial == "poiseuille":
        y0, y1 = scenario.domain.bounds[1]
        radius = 0.5 * (y1 - y0)
        center = 0.5 * (y0 + y1)
        velocity[:, :, 0] = 1.5 * (1.0 - ((centers[:, :, 1] - center) / radius) ** 2)
        velocity[:, :, 1] = 0.0
    return velocity


class PicFlipSolver:
    info = SolverInfo(
        id="pic-flip",
        display_name="Blended PIC/FLIP",
        dimensions=(2,),
        supports_moving_boundary=True,
        acceleration="Numba quadratic transfer + NumPy/SciPy grid",
    )

    def __init__(self) -> None:
        self._scenario: Scenario | None = None
        self._geometry: NacaFoil | None = None
        self._positions: PointCloud | None = None
        self._particle_velocity: ParticleVelocity | None = None
        self._grid_velocity: VelocityField | None = None
        self._solid: MaskField | None = None
        self._centers: CoordinateField | None = None
        self._solid_angle: float | None = None
        self._control: ControlState = ControlState(0.0, 0.0, 0.0)
        self._time: float = 0.0
        self._blend: float = 0.95
        self._settling_steps: int = 0
        self._reynolds: float = 1.0
        self._rng: PCG32 = PCG32(0)
        self._projection_warning: str = ""
        self._last_projection: IterativeReport = IterativeReport(
            "relative-l2", 0.0, 0, 0.0, True
        )
        self._last_viscosity: IterativeReport = IterativeReport(
            "update-linf", 0.0, 0, 0.0, True
        )
        self._reseeded_last_step: int = 0
        self._advance_count: int = 0
        self._population_interval: int = 8
        self._cfl: float = 0.75
        self._swept_collisions_last_step: int = 0
        self._revision: int = 0
        self._unsupported_face_fraction: float = 0.0
        self._native_divergence_linf: float | None = None
        self._native_solid_leakage: float | None = None

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
    def blend(self) -> float:
        return self._blend

    @blend.setter
    def blend(self, value: float) -> None:
        if not np.isfinite(value):
            raise ValueError("PIC/FLIP blend must be finite")
        selected = float(np.clip(value, 0.0, 1.0))
        if selected != self._blend and self._scenario is not None:
            self._revision += 1
        self._blend = selected

    def interactive_tuning(self) -> InteractiveTuning:
        return InteractiveTuning(
            "pic-flip-blend",
            "blend",
            self._blend,
            f"{self._blend:.2f}",
            self._blend > 0.0,
            self._blend < 1.0,
        )

    def adjust_interactive_tuning(self, direction: int) -> InteractiveTuning:
        self.blend = self._blend + (-0.05 if direction < 0 else 0.05)
        return self.interactive_tuning()

    def apply_interactive_tuning(self, value: str | float) -> InteractiveTuning:
        if not isinstance(value, (int, float)):
            raise TypeError("PIC/FLIP tuning value must be numeric")
        self.blend = float(value)
        return self.interactive_tuning()

    def _require(
        self,
    ) -> tuple[Scenario, NacaFoil, PointCloud, ParticleVelocity, VelocityField, MaskField]:
        if (
            self._scenario is None
            or self._geometry is None
            or self._positions is None
            or self._particle_velocity is None
            or self._grid_velocity is None
            or self._solid is None
        ):
            raise RuntimeError("solver has not been initialized")
        return (
            self._scenario,
            self._geometry,
            self._positions,
            self._particle_velocity,
            self._grid_velocity,
            self._solid,
        )

    def initialize(self, scenario: Scenario, geometry: NacaFoil, seed: int) -> None:
        if scenario.domain.dimension != 2:
            raise NotImplementedError("PIC/FLIP Phase 1 supports only 2D")
        self._scenario = scenario
        self._geometry = geometry
        self._control = scenario.control_at(0.0)
        self._time = 0.0
        self._reynolds = float(scenario.reynolds)
        self._revision = 0
        self._unsupported_face_fraction = 0.0
        self._native_divergence_linf = None
        self._native_solid_leakage = None
        self._last_projection = IterativeReport("relative-l2", 0.0, 0, 0.0, True)
        self._last_viscosity = IterativeReport("update-linf", 0.0, 0, 0.0, True)
        configured_blend = scenario.solver_options.get("pic_flip_blend", 0.95)
        if not isinstance(configured_blend, (int, float)):
            raise TypeError("pic_flip_blend must be numeric")
        self.blend = float(configured_blend)
        configured_interval = scenario.solver_options.get("pic_population_interval", 8)
        if not isinstance(configured_interval, int) or isinstance(configured_interval, bool):
            raise TypeError("pic_population_interval must be an integer")
        if configured_interval < 1:
            raise ValueError("pic_population_interval must be positive")
        self._population_interval = configured_interval
        configured_cfl = scenario.solver_options.get("pic_cfl", 0.75)
        if not isinstance(configured_cfl, (int, float)):
            raise TypeError("pic_cfl must be numeric")
        if not np.isfinite(configured_cfl) or not 0.0 < float(configured_cfl) <= 1.0:
            raise ValueError("pic_cfl must be in (0, 1]")
        self._cfl = float(configured_cfl)
        self._advance_count = 0
        self._rng = PCG32(seed, stream=71)
        self._solid = geometry.mask(scenario.domain, self._control.angle_degrees)
        self._solid_angle = self._control.angle_degrees
        centers = cell_centers(scenario.domain)
        self._centers = centers
        velocity = _initial_velocity(scenario)
        self._grid_velocity = velocity
        self._seed_particles(velocity)
        self._particle_to_grid()
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
        self._solid_angle = start.angle_degrees
        velocity = _initial_velocity(scenario)
        centers = cell_centers(scenario.domain)
        wall = geometry.wall_velocity(
            centers.reshape(-1, 2),
            self._control,
        ).reshape(velocity.shape)
        velocity[self._solid] = wall[self._solid]
        self._grid_velocity = velocity
        self._seed_particles(velocity)
        self._particle_to_grid()
        self._revision = 0
        self._unsupported_face_fraction = 0.0

    def _seed_particles(self, velocity: VelocityField) -> None:
        scenario, _, _, _, _, solid = self._require_seed()
        fluid_y, fluid_x = np.nonzero(~solid)
        per_cell = 4
        cell_x = np.repeat(fluid_x, per_cell)
        cell_y = np.repeat(fluid_y, per_cell)
        count = cell_x.size
        jitter = self._rng.random((count, 2)).astype(scenario.dtype)
        positions = np.empty((count, 2), dtype=scenario.dtype)
        positions[:, 0] = (
            scenario.domain.bounds[0][0] + (cell_x + 0.1 + 0.8 * jitter[:, 0]) * scenario.domain.dx
        )
        positions[:, 1] = (
            scenario.domain.bounds[1][0] + (cell_y + 0.1 + 0.8 * jitter[:, 1]) * scenario.domain.dy
        )
        self._positions = positions
        self._particle_velocity = self._grid_to_particle(velocity, positions)

    def _require_seed(
        self,
    ) -> tuple[
        Scenario,
        NacaFoil,
        PointCloud | None,
        ParticleVelocity | None,
        VelocityField,
        MaskField,
    ]:
        if (
            self._scenario is None
            or self._geometry is None
            or self._grid_velocity is None
            or self._solid is None
        ):
            raise RuntimeError("solver has not been initialized")
        return (
            self._scenario,
            self._geometry,
            self._positions,
            self._particle_velocity,
            self._grid_velocity,
            self._solid,
        )

    def _particle_to_grid(self) -> VelocityField:
        scenario, _, positions, particle_velocity, grid_velocity, _ = self._require()
        fallback_u, fallback_v = cell_to_faces(grid_velocity)
        u, v, unsupported = particle_to_faces(
            positions,
            particle_velocity,
            fallback_u,
            fallback_v,
            scenario.domain.bounds[0][0],
            scenario.domain.bounds[1][0],
            scenario.domain.dx,
            scenario.domain.dy,
            "x" in scenario.domain.periodic_axes,
            "y" in scenario.domain.periodic_axes,
        )
        self._unsupported_face_fraction = unsupported
        return faces_to_cell(u, v)

    def _grid_to_particle(
        self,
        velocity: VelocityField,
        positions: PointCloud,
    ) -> ParticleVelocity:
        if self._scenario is None:
            raise RuntimeError("solver has not been initialized")
        u, v = cell_to_faces(velocity)
        return self._faces_to_particle(u, v, positions)

    def _faces_to_particle(
        self,
        u: FaceVelocityX,
        v: FaceVelocityY,
        positions: PointCloud,
    ) -> ParticleVelocity:
        if self._scenario is None:
            raise RuntimeError("solver has not been initialized")
        domain = self._scenario.domain
        return faces_to_particle(
            u,
            v,
            positions,
            domain.bounds[0][0],
            domain.bounds[1][0],
            domain.dx,
            domain.dy,
            "x" in domain.periodic_axes,
            "y" in domain.periodic_axes,
        )

    def _particle_to_faces(
        self,
        fallback_u: FaceVelocityX,
        fallback_v: FaceVelocityY,
    ) -> tuple[FaceVelocityX, FaceVelocityY]:
        scenario, _, positions, particle_velocity, _, _ = self._require()
        u, v, unsupported = particle_to_faces(
            positions,
            particle_velocity,
            fallback_u,
            fallback_v,
            scenario.domain.bounds[0][0],
            scenario.domain.bounds[1][0],
            scenario.domain.dx,
            scenario.domain.dy,
            "x" in scenario.domain.periodic_axes,
            "y" in scenario.domain.periodic_axes,
        )
        self._unsupported_face_fraction = unsupported
        return u, v

    def _project(self, velocity: VelocityField, control: ControlState, dt: float) -> VelocityField:
        u, v = cell_to_faces(velocity)
        projected_u, projected_v = self._project_faces(u, v, control, dt)
        return faces_to_cell(projected_u, projected_v)

    def _project_faces(
        self,
        u: FaceVelocityX,
        v: FaceVelocityY,
        control: ControlState,
        dt: float,
    ) -> tuple[FaceVelocityX, FaceVelocityY]:
        scenario, geometry, _, _, _, solid = self._require()
        if self._centers is None:
            raise RuntimeError("PIC/FLIP grid cache has not been initialized")
        points = self._centers.reshape(-1, 2)
        wall = geometry.wall_velocity(points, control).reshape(
            scenario.domain.ny, scenario.domain.nx, 2
        )
        channel = str(scenario.solver_options.get("initial_condition", "")) == "poiseuille"
        pressure_tolerance = _float_option(scenario, "pressure_tolerance", 1.0e-5)
        iterations_option = _int_option(scenario, "pressure_max_iterations", 640)
        u, v, report = project_faces(
            u,
            v,
            scenario.domain,
            solid,
            wall,
            scenario.freestream,
            dt,
            channel,
            pressure_tolerance,
            iterations_option,
        )
        self._last_projection = report
        if not report.converged:
            raise NumericalFailure(
                "projection_failure",
                "PIC/FLIP pressure solve did not converge",
                "projection",
                {
                    "criterion": report.criterion,
                    "iterations": report.iterations,
                    "tolerance": report.tolerance,
                    "relative_residual": report.final_residual,
                },
            )
        self._projection_warning = ""
        return u, v

    def _advect_particles(
        self,
        start_control: ControlState,
        control: ControlState,
        dt: float,
        velocity_0: ParticleVelocity,
        u: FaceVelocityX,
        v: FaceVelocityY,
    ) -> None:
        scenario, _geometry, positions, particle_velocity, _grid_velocity, _solid = self._require()
        start_positions = positions.copy()
        midpoint = positions + 0.5 * dt * velocity_0
        velocity_mid = self._faces_to_particle(u, v, midpoint)
        positions += dt * velocity_mid
        self._swept_collisions_last_step += self._resolve_swept_particle_collisions(
            start_positions,
            start_control,
            control,
        )
        x0, x1 = scenario.domain.bounds[0]
        y0, y1 = scenario.domain.bounds[1]
        periodic_x = "x" in scenario.domain.periodic_axes
        periodic_y = "y" in scenario.domain.periodic_axes
        if periodic_x:
            positions[:, 0] = x0 + np.mod(positions[:, 0] - x0, x1 - x0)
        if periodic_y:
            positions[:, 1] = y0 + np.mod(positions[:, 1] - y0, y1 - y0)
        escaped = np.zeros(positions.shape[0], dtype=np.bool_)
        if not periodic_x:
            escaped |= (positions[:, 0] < x0) | (positions[:, 0] > x1)
        if not periodic_y:
            escaped |= (positions[:, 1] < y0) | (positions[:, 1] > y1)
        count = int(np.count_nonzero(escaped))
        if count:
            if periodic_x:
                positions[escaped, 0] = self._rng.uniform(x0, x1, (count,))
            else:
                positions[escaped, 0] = x0 + self._rng.uniform(
                    0.0,
                    0.5 * scenario.domain.dx,
                    (count,),
                )
            positions[escaped, 1] = self._rng.uniform(y0, y1, (count,))
            particle_velocity[escaped] = np.asarray(scenario.freestream[:2], dtype=scenario.dtype)
        self._resolve_particle_collisions(control)

    def _resolve_swept_particle_collisions(
        self,
        start_positions: PointCloud,
        start_control: ControlState,
        control: ControlState,
    ) -> int:
        scenario, geometry, positions, particle_velocity, _, _ = self._require()
        path = positions - start_positions
        pivot = np.asarray(scenario.foil.pivot[:2], dtype=positions.dtype)
        path_length_squared = np.sum(path * path, axis=1)
        projection = np.sum((pivot - start_positions) * path, axis=1) / np.maximum(
            path_length_squared,
            1.0e-12,
        )
        projection = np.clip(projection, 0.0, 1.0)
        closest = start_positions + projection[:, None] * path
        collision_margin = 0.05 * min(scenario.domain.dx, scenario.domain.dy)
        candidate = np.linalg.norm(closest - pivot, axis=1) <= (
            geometry.maximum_radius + collision_margin
        )
        candidate_indices = np.flatnonzero(candidate)
        if candidate_indices.size == 0:
            return 0

        particle_travel = float(
            np.max(np.linalg.norm(path[candidate_indices], axis=1))
        )
        wall_travel = (
            abs(np.deg2rad(control.angle_degrees - start_control.angle_degrees))
            * geometry.maximum_radius
        )
        spacing = 0.1 * min(scenario.domain.dx, scenario.domain.dy)
        sample_count = int(
            np.clip(
                np.ceil((particle_travel + wall_travel) / max(spacing, 1.0e-12)),
                2,
                16,
            )
        )
        hit = np.zeros(candidate_indices.size, dtype=np.bool_)
        for sample in range(1, sample_count + 1):
            fraction = sample / sample_count
            angle = start_control.angle_degrees + fraction * (
                control.angle_degrees - start_control.angle_degrees
            )
            sample_points = (
                start_positions[candidate_indices]
                + fraction * path[candidate_indices]
            )
            distance = geometry.signed_distance(sample_points, angle)
            entering = (distance <= collision_margin) & ~hit
            if not np.any(entering):
                continue
            selected = candidate_indices[entering]
            inside_points = sample_points[entering]
            inside_distance = distance[entering]
            normal = geometry.normals(inside_points, angle)
            resolved = inside_points - (
                inside_distance[:, None] - collision_margin
            ) * normal
            positions[selected] = resolved
            collision_control = ControlState(
                start_control.time + fraction * (control.time - start_control.time),
                angle,
                control.angular_velocity_degrees,
            )
            wall = geometry.wall_velocity(resolved, collision_control)
            relative = particle_velocity[selected] - wall
            inward_speed = np.sum(relative * normal, axis=1)
            into_wall = inward_speed < 0.0
            relative[into_wall] -= (
                inward_speed[into_wall, None] * normal[into_wall]
            )
            particle_velocity[selected] = wall + relative
            hit[entering] = True
        return int(np.count_nonzero(hit))

    def _resolve_particle_collisions(self, control: ControlState) -> None:
        _, geometry, positions, particle_velocity, _, _ = self._require()
        inside = geometry.contains(positions, control.angle_degrees)
        if np.any(inside):
            points = positions[inside]
            distance = geometry.signed_distance(points, control.angle_degrees)
            normal = geometry.normals(points, control.angle_degrees)
            positions[inside] -= (distance[:, None] - 1.0e-4) * normal
            wall = geometry.wall_velocity(positions[inside], control)
            relative = particle_velocity[inside] - wall
            into_wall = np.sum(relative * normal, axis=1) < 0.0
            relative[into_wall] -= (
                np.sum(relative[into_wall] * normal[into_wall], axis=1)[:, None] * normal[into_wall]
            )
            particle_velocity[inside] = wall + relative

    def _maintain_particle_population(self, control: ControlState) -> None:
        scenario, _, positions, particle_velocity, grid_velocity, solid = self._require()
        report = maintain_particle_population(
            positions,
            solid,
            scenario.domain,
            self._rng,
        )
        moved = report.moved_indices
        if moved.size:
            particle_velocity[moved] = self._grid_to_particle(
                grid_velocity,
                positions[moved],
            )
            self._reseeded_last_step += int(moved.size)
            self._resolve_particle_collisions(control)

    def advance(self, control: ControlState, target_dt: float) -> StepReport:
        scenario, geometry, positions, particle_velocity, grid_velocity, _ = self._require()
        centers = self._centers
        if centers is None:
            raise RuntimeError("PIC/FLIP grid cache has not been initialized")
        validate_advance_request(self._time, control, target_dt, scenario.precision)
        if not (
            np.isfinite(positions).all()
            and np.isfinite(particle_velocity).all()
            and np.isfinite(grid_velocity).all()
        ):
            raise NumericalFailure(
                "nonfinite_state",
                "PIC/FLIP input state is non-finite",
                "postcondition",
            )
        max_speed = max(
            float(np.max(np.linalg.norm(grid_velocity, axis=2))),
            abs(scenario.freestream[0]),
            1.0e-6,
        )
        boundary_angular_velocity = control.angular_velocity_degrees
        pose_sweep_angular_velocity = (
            control.angle_degrees - self._control.angle_degrees
        ) / target_dt
        wall_speed = geometry.maximum_radius * max(
            abs(np.deg2rad(boundary_angular_velocity)),
            abs(np.deg2rad(pose_sweep_angular_velocity)),
        )
        transport_speed = max(max_speed, wall_speed)
        stable_dt = (
            self._cfl
            * min(scenario.domain.dx, scenario.domain.dy)
            / max(transport_speed, 1.0e-6)
        )
        substeps = max(1, int(np.ceil(target_dt / stable_dt)))
        if substeps > 512:
            raise NumericalFailure(
                "stability_limit",
                "PIC/FLIP motion requires too many internal substeps",
                "particle-advection",
                {
                    "required_substeps": substeps,
                    "maximum_substeps": 512,
                    "maximum_particle_speed": max_speed,
                    "maximum_wall_speed": wall_speed,
                },
            )
        dt = target_dt / substeps
        solid = self._solid
        if solid is None:
            raise RuntimeError("solver has not been initialized")
        checkpoint: PicAdvanceCheckpoint = (
            positions.copy(),
            particle_velocity.copy(),
            grid_velocity.copy(),
            solid.copy(),
            self._solid_angle,
            self._control,
            self._time,
            self._rng.checkpoint(),
            self._projection_warning,
            self._last_projection,
            self._last_viscosity,
            self._reseeded_last_step,
            self._swept_collisions_last_step,
            self._advance_count,
            self._settling_steps,
            self._revision,
            self._unsupported_face_fraction,
        )
        start_time = self._time
        start_angle = self._control.angle_degrees
        report_speed = 0.0
        counts = np.empty(0, dtype=np.int64)
        projected_u, projected_v = cell_to_faces(grid_velocity)
        try:
            self._reseeded_last_step = 0
            self._swept_collisions_last_step = 0
            for substep in range(substeps):
                fraction = (substep + 1) / substeps
                sub_control = ControlState(
                    start_time + fraction * target_dt,
                    start_angle + fraction * (control.angle_degrees - start_angle),
                    boundary_angular_velocity,
                )
                if self._solid_angle != sub_control.angle_degrees:
                    self._solid = geometry.mask(scenario.domain, sub_control.angle_degrees)
                    self._solid_angle = sub_control.angle_degrees
                start_control = self._control
                self._resolve_particle_collisions(sub_control)
                fallback_grid = self._grid_velocity
                if fallback_grid is None:
                    raise RuntimeError("PIC/FLIP grid state is missing")
                fallback_u, fallback_v = cell_to_faces(fallback_grid)
                transferred_u, transferred_v = self._particle_to_faces(
                    fallback_u,
                    fallback_v,
                )
                pre_projection_u = transferred_u.copy()
                pre_projection_v = transferred_v.copy()
                viscosity = (
                    scenario.reference_speed * scenario.foil.chord / self._reynolds
                )
                diffused_u, diffused_v, self._last_viscosity = implicit_diffuse_faces(
                    transferred_u,
                    transferred_v,
                    viscosity,
                    dt,
                    scenario.domain,
                    _float_option(scenario, "pressure_tolerance", 1.0e-5),
                    _int_option(scenario, "pressure_max_iterations", 640),
                )
                if not self._last_viscosity.converged:
                    raise NumericalFailure(
                        "convergence_failure",
                        "PIC/FLIP implicit viscosity did not converge",
                        "viscosity",
                        {
                            "criterion": self._last_viscosity.criterion,
                            "iterations": self._last_viscosity.iterations,
                            "tolerance": self._last_viscosity.tolerance,
                            "final_residual": self._last_viscosity.final_residual,
                        },
                    )
                projected_u, projected_v = self._project_faces(
                    diffused_u,
                    diffused_v,
                    sub_control,
                    dt,
                )
                self._grid_velocity = faces_to_cell(projected_u, projected_v)
                pic_velocity = self._faces_to_particle(
                    projected_u,
                    projected_v,
                    positions,
                )
                delta = self._faces_to_particle(
                    projected_u - pre_projection_u,
                    projected_v - pre_projection_v,
                    positions,
                )
                blend = 0.0 if self._settling_steps > 0 else self._blend
                particle_velocity[:] = (1.0 - blend) * pic_velocity + blend * (
                    particle_velocity + delta
                )
                self._control = sub_control
                self._advect_particles(
                    start_control,
                    sub_control,
                    dt,
                    pic_velocity,
                    projected_u,
                    projected_v,
                )
                if self._settling_steps > 0:
                    self._settling_steps -= 1
            self._advance_count += 1
            if self._advance_count % self._population_interval == 0:
                self._maintain_particle_population(control)
            final_grid = self._grid_velocity
            if final_grid is None or not (
                np.isfinite(final_grid).all()
                and np.isfinite(positions).all()
                and np.isfinite(particle_velocity).all()
            ):
                raise NumericalFailure(
                    "nonfinite_state",
                    "PIC/FLIP produced non-finite state",
                    "postcondition",
                )
            self._resolve_particle_collisions(control)
            inside = geometry.contains(positions, control.angle_degrees)
            if np.any(inside):
                raise NumericalFailure(
                    "postcondition_failure",
                    "PIC/FLIP left particles inside the moving solid",
                    "postcondition",
                    {"unresolved_solid_particles": int(np.count_nonzero(inside))},
                )
            counts = particle_cell_counts(positions, scenario.domain)
            report_speed = max(
                transport_speed,
                float(np.max(np.linalg.norm(final_grid, axis=2))),
                float(np.max(np.linalg.norm(particle_velocity, axis=1))),
            )
            if not np.isfinite(report_speed):
                raise NumericalFailure(
                    "nonfinite_state",
                    "PIC/FLIP produced a non-finite step report",
                    "postcondition",
                )
            accepted_cfl = dt * report_speed / min(
                scenario.domain.dx,
                scenario.domain.dy,
            )
            if accepted_cfl > self._cfl * (1.0 + 1.0e-6):
                raise NumericalFailure(
                    "stability_limit",
                    "PIC/FLIP post-step motion exceeded its swept envelope",
                    "particle-advection",
                    {
                        "accepted_cfl": accepted_cfl,
                        "maximum_cfl": self._cfl,
                    },
                )
        except Exception:
            (
                self._positions,
                self._particle_velocity,
                self._grid_velocity,
                self._solid,
                self._solid_angle,
                self._control,
                self._time,
                rng_checkpoint,
                self._projection_warning,
                self._last_projection,
                self._last_viscosity,
                self._reseeded_last_step,
                self._swept_collisions_last_step,
                self._advance_count,
                self._settling_steps,
                self._revision,
                self._unsupported_face_fraction,
            ) = checkpoint
            self._rng.restore(rng_checkpoint)
            raise
        self._time = start_time + target_dt
        self._control = ControlState(
            self._time,
            control.angle_degrees,
            control.angular_velocity_degrees,
        )
        self._revision += 1
        warnings = () if not self._projection_warning else (self._projection_warning,)
        _, _, _, _, _, final_solid = self._require()
        final_wall = geometry.wall_velocity(
            centers.reshape(-1, 2),
            self._control,
        ).reshape(scenario.domain.ny, scenario.domain.nx, 2)
        native_divergence = native_divergence_linf(
            projected_u,
            projected_v,
            scenario.domain,
            final_solid,
        )
        native_leakage = solid_face_leakage(
            projected_u,
            projected_v,
            final_solid,
            final_wall,
        )
        self._native_divergence_linf = native_divergence
        self._native_solid_leakage = native_leakage
        fluid_counts = counts[~final_solid.reshape(-1)]
        empty_fraction = float(np.count_nonzero(fluid_counts == 0)) / max(
            1,
            fluid_counts.size,
        )
        underfilled_fraction = float(np.count_nonzero(fluid_counts < 4)) / max(
            1,
            fluid_counts.size,
        )
        return StepReport(
            target_dt,
            target_dt,
            substeps,
            report_speed,
            warnings,
            self._revision,
            {
                "maximum_particle_speed": float(
                    np.max(np.linalg.norm(particle_velocity, axis=1))
                ),
                "maximum_wall_speed": wall_speed,
                "maximum_particle_cfl": dt * report_speed / min(
                    scenario.domain.dx,
                    scenario.domain.dy,
                ),
                "maximum_characteristic_displacement": dt
                * report_speed
                / min(scenario.domain.dx, scenario.domain.dy),
                "particle_count": positions.shape[0],
                "empty_cell_fraction": empty_fraction,
                "underfilled_cell_fraction": underfilled_fraction,
                "unresolved_solid_particles": 0,
                "unsupported_face_fraction": self._unsupported_face_fraction,
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

    def sample_velocity(self, points: PointCloud) -> PointCloud:
        scenario, _, _, _, grid_velocity, _ = self._require()
        return sample_vector(grid_velocity, points, scenario.domain)

    def export_state(self) -> CanonicalFlowState:
        scenario, _, _, _, grid_velocity, solid = self._require()
        velocity = grid_velocity.copy()
        velocity[solid] = 0.0
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
            velocity=velocity[None, ...],
        )

    def import_state(self, state: CanonicalFlowState, control: ControlState) -> ImportOutcome:
        scenario, geometry, positions, particle_velocity, grid_velocity, solid = self._require()
        checkpoint: PicImportCheckpoint = (
            positions.copy(),
            particle_velocity.copy(),
            grid_velocity.copy(),
            solid.copy(),
            self._solid_angle,
            self._control,
            self._time,
            self._rng.checkpoint(),
            self._settling_steps,
            self._revision,
            self._unsupported_face_fraction,
        )
        try:
            validate_canonical_import(state, scenario, control)
            velocity = np.asarray(state.velocity[0], dtype=scenario.dtype).copy()
            self._time = state.time
            self._control = control
            self._solid = geometry.mask(scenario.domain, control.angle_degrees)
            self._solid_angle = control.angle_degrees
            if self._centers is None:
                raise RuntimeError("PIC/FLIP grid cache has not been initialized")
            wall = geometry.wall_velocity(
                self._centers.reshape(-1, 2), control
            ).reshape(scenario.domain.ny, scenario.domain.nx, 2)
            velocity[self._solid] = wall[self._solid]
            self._grid_velocity = velocity
            self._seed_particles(self._grid_velocity)
            self._settling_steps = 1
            self._native_divergence_linf = None
            self._native_solid_leakage = None
        except NumericalFailure as failure:
            (
                self._positions,
                self._particle_velocity,
                self._grid_velocity,
                self._solid,
                self._solid_angle,
                self._control,
                self._time,
                rng_checkpoint,
                self._settling_steps,
                self._revision,
                self._unsupported_face_fraction,
            ) = checkpoint
            self._rng.restore(rng_checkpoint)
            return ImportOutcome(
                "rejected",
                failure.reason,
                warnings=(str(failure),),
                stage=failure.stage,
                evidence=failure.evidence,
            )
        except Exception:
            (
                self._positions,
                self._particle_velocity,
                self._grid_velocity,
                self._solid,
                self._solid_angle,
                self._control,
                self._time,
                rng_checkpoint,
                self._settling_steps,
                self._revision,
                self._unsupported_face_fraction,
            ) = checkpoint
            self._rng.restore(rng_checkpoint)
            raise
        self._revision += 1
        report = ImportReport(
            state.source_solver,
            self.info.id,
            ("solver particles", "FLIP velocity delta history"),
            ("The first imported step is PIC-dominant while FLIP history is rebuilt.",),
        )
        return ImportOutcome("accepted", "none", report, report.warnings)

    def diagnostics(self) -> Diagnostics:
        scenario, geometry, positions, _, grid_velocity, solid = self._require()
        counts = particle_cell_counts(positions, scenario.domain).reshape(
            scenario.domain.ny,
            scenario.domain.nx,
        )
        fluid_counts = counts[~solid]
        values = {
            "time": self._time,
            "requested_reynolds": self._reynolds,
            "kinetic_energy": kinetic_energy(grid_velocity),
            "enstrophy": enstrophy(grid_velocity, scenario.domain),
            "divergence_l2": divergence_l2(grid_velocity, scenario.domain),
            "particle_count": float(positions.shape[0]),
            "unsupported_face_fraction": self._unsupported_face_fraction,
            "empty_fluid_cell_fraction": float(np.count_nonzero(fluid_counts == 0))
            / fluid_counts.size,
            "underfilled_fluid_cell_fraction": float(np.count_nonzero(fluid_counts < 4))
            / fluid_counts.size,
            "p05_particles_per_fluid_cell": float(np.percentile(fluid_counts, 5.0)),
            "p95_particles_per_fluid_cell": float(np.percentile(fluid_counts, 95.0)),
            "max_particles_per_fluid_cell": float(np.max(fluid_counts)),
            "reseeded_last_step": float(self._reseeded_last_step),
            "swept_collisions_last_step": float(self._swept_collisions_last_step),
            "particles_inside_solid": float(
                np.count_nonzero(
                    geometry.contains(
                        positions,
                        self._control.angle_degrees,
                    )
                )
            ),
            "wake_width": wake_width(grid_velocity, scenario.domain, scenario.foil.pivot[0]),
            "recirculation_area": recirculation_area(
                grid_velocity, scenario.domain, scenario.foil.pivot[0]
            ),
        }
        if self._native_divergence_linf is not None:
            values["divergence_linf"] = self._native_divergence_linf
        if self._native_solid_leakage is not None:
            values["solid_leakage"] = self._native_solid_leakage
        if not all(np.isfinite(value) for value in values.values()):
            raise NumericalFailure(
                "nonfinite_state",
                "PIC/FLIP produced non-finite diagnostics",
            )
        warnings = () if not self._projection_warning else (self._projection_warning,)
        return Diagnostics(values, warnings, self._revision)
