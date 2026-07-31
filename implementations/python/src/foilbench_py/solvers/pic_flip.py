"""Blended PIC/FLIP reference with quadratic B-spline transfers."""

import numpy as np

from foilbench_py.core.geometry import NacaFoil, cell_centers
from foilbench_py.core.grid import cell_to_faces, faces_to_cell, project_faces
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
from foilbench_py.core.rng import PCG32
from foilbench_py.solvers._numba_adapter import grid_to_particle, particle_to_grid
from foilbench_py.types import MaskField, ParticleVelocity, PointCloud, VelocityField


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
        self._control = ControlState(0.0, 0.0, 0.0)
        self._time = 0.0
        self._blend = 0.95
        self._settling_steps = 0
        self._rng = PCG32(0)
        self._projection_warning = ""

    @property
    def blend(self) -> float:
        return self._blend

    @blend.setter
    def blend(self, value: float) -> None:
        self._blend = float(np.clip(value, 0.0, 1.0))

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
        configured_blend = scenario.solver_options.get("pic_flip_blend", 0.95)
        if not isinstance(configured_blend, (int, float)):
            raise TypeError("pic_flip_blend must be numeric")
        self._blend = float(configured_blend)
        self._rng = PCG32(seed, stream=71)
        self._solid = geometry.mask(scenario.domain, self._control.angle_degrees)
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
        self._grid_velocity = velocity
        self._seed_particles(velocity)
        self._particle_to_grid()

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
        scenario, _, positions, particle_velocity, _, _ = self._require()
        return particle_to_grid(
            positions,
            particle_velocity,
            scenario.domain.bounds[0][0],
            scenario.domain.bounds[1][0],
            scenario.domain.dx,
            scenario.domain.dy,
            scenario.domain.nx,
            scenario.domain.ny,
            scenario.freestream,
        )

    def _grid_to_particle(
        self,
        velocity: VelocityField,
        positions: PointCloud,
    ) -> ParticleVelocity:
        if self._scenario is None:
            raise RuntimeError("solver has not been initialized")
        domain = self._scenario.domain
        return grid_to_particle(
            velocity,
            positions,
            domain.bounds[0][0],
            domain.bounds[1][0],
            domain.dx,
            domain.dy,
        )

    def _project(self, velocity: VelocityField, control: ControlState, dt: float) -> VelocityField:
        scenario, geometry, _, _, _, solid = self._require()
        u, v = cell_to_faces(velocity)
        points = cell_centers(scenario.domain).reshape(-1, 2)
        wall = geometry.wall_velocity(points, control).reshape(
            scenario.domain.ny, scenario.domain.nx, 2
        )
        channel = str(scenario.solver_options.get("initial_condition", "")) == "poiseuille"
        tolerance_option = scenario.solver_options.get("pressure_tolerance", 1.0e-5)
        if not isinstance(tolerance_option, (int, float)):
            raise TypeError("pressure_tolerance must be numeric")
        pressure_tolerance = float(tolerance_option)
        u, v, info = project_faces(
            u,
            v,
            scenario.domain,
            solid,
            wall,
            scenario.freestream,
            dt,
            channel,
            pressure_tolerance,
        )
        self._projection_warning = "" if info == 0 else f"pressure CG returned {info}"
        return faces_to_cell(u, v)

    def _advect_particles(self, control: ControlState, dt: float) -> None:
        scenario, geometry, positions, particle_velocity, grid_velocity, _ = self._require()
        velocity_0 = self._grid_to_particle(grid_velocity, positions)
        midpoint = positions + 0.5 * dt * velocity_0
        velocity_mid = self._grid_to_particle(grid_velocity, midpoint)
        positions += dt * velocity_mid
        x0, x1 = scenario.domain.bounds[0]
        y0, y1 = scenario.domain.bounds[1]
        escaped = (
            (positions[:, 0] < x0)
            | (positions[:, 0] > x1)
            | (positions[:, 1] < y0)
            | (positions[:, 1] > y1)
        )
        count = int(np.count_nonzero(escaped))
        if count:
            positions[escaped, 0] = x0 + self._rng.uniform(0.0, 0.5 * scenario.domain.dx, (count,))
            positions[escaped, 1] = self._rng.uniform(y0, y1, (count,))
            particle_velocity[escaped] = np.asarray(scenario.freestream[:2], dtype=scenario.dtype)
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

    def advance(self, control: ControlState, target_dt: float) -> StepReport:
        scenario, geometry, positions, particle_velocity, grid_velocity, _ = self._require()
        if target_dt <= 0.0:
            raise ValueError("target_dt must be positive")
        max_speed = max(
            float(np.max(np.linalg.norm(grid_velocity, axis=2))),
            abs(scenario.freestream[0]),
            1.0e-6,
        )
        stable_dt = 0.6 * min(scenario.domain.dx, scenario.domain.dy) / max_speed
        substeps = max(1, int(np.ceil(target_dt / stable_dt)))
        dt = target_dt / substeps
        for substep in range(substeps):
            fraction = (substep + 1) / substeps
            sub_control = ControlState(
                self._time + fraction * target_dt,
                self._control.angle_degrees
                + fraction * (control.angle_degrees - self._control.angle_degrees),
                control.angular_velocity_degrees,
            )
            self._solid = geometry.mask(scenario.domain, sub_control.angle_degrees)
            transferred = self._particle_to_grid()
            pre_projection_grid = transferred.copy()
            self._grid_velocity = self._project(transferred, sub_control, dt)
            pic_velocity = self._grid_to_particle(self._grid_velocity, positions)
            delta = self._grid_to_particle(
                self._grid_velocity - pre_projection_grid,
                positions,
            )
            blend = 0.0 if self._settling_steps > 0 else self._blend
            particle_velocity[:] = (1.0 - blend) * pic_velocity + blend * (
                particle_velocity + delta
            )
            self._control = sub_control
            self._advect_particles(sub_control, dt)
            if self._settling_steps > 0:
                self._settling_steps -= 1
        self._time += target_dt
        self._control = ControlState(
            self._time, control.angle_degrees, control.angular_velocity_degrees
        )
        warnings = () if not self._projection_warning else (self._projection_warning,)
        return StepReport(target_dt, target_dt, substeps, max_speed, warnings)

    def sample_velocity(self, points: PointCloud) -> PointCloud:
        scenario, _, _, _, grid_velocity, _ = self._require()
        return sample_vector(grid_velocity, points, scenario.domain)

    def export_state(self) -> CanonicalFlowState:
        scenario, _, _, _, grid_velocity, _ = self._require()
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
            velocity=grid_velocity[None, ...],
        )

    def import_state(self, state: CanonicalFlowState, control: ControlState) -> ImportReport:
        scenario, geometry, _, _, _, _ = self._require()
        if state.dimension != 2 or state.resolution != scenario.domain.resolution:
            raise ValueError("warm import requires the same 2D resolution")
        self._grid_velocity = np.asarray(state.velocity[0], dtype=scenario.dtype).copy()
        self._time = state.time
        self._control = control
        self._solid = geometry.mask(scenario.domain, control.angle_degrees)
        self._seed_particles(self._grid_velocity)
        self._settling_steps = 1
        return ImportReport(
            state.source_solver,
            self.info.id,
            ("solver particles", "FLIP velocity delta history"),
            ("The first imported step is PIC-dominant while FLIP history is rebuilt.",),
        )

    def diagnostics(self) -> Diagnostics:
        scenario, _, positions, _, grid_velocity, solid = self._require()
        values = {
            "time": self._time,
            "kinetic_energy": kinetic_energy(grid_velocity),
            "enstrophy": enstrophy(grid_velocity, scenario.domain),
            "divergence_l2": divergence_l2(grid_velocity, scenario.domain),
            "solid_leakage": solid_leakage(grid_velocity, solid),
            "particle_count": float(positions.shape[0]),
            "wake_width": wake_width(grid_velocity, scenario.domain, scenario.foil.pivot[0]),
            "recirculation_area": recirculation_area(
                grid_velocity, scenario.domain, scenario.foil.pivot[0]
            ),
        }
        if not all(np.isfinite(value) for value in values.values()):
            raise FloatingPointError("PIC/FLIP produced non-finite diagnostics")
        warnings = () if not self._projection_warning else (self._projection_warning,)
        return Diagnostics(values, warnings)
