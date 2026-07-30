# pyright: reportPrivateImportUsage=false
"""Vectorized D2Q9 two-relaxation-time lattice Boltzmann reference."""

import einx
import numpy as np

from foilbench_py.core.geometry import NacaFoil, cell_centers
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
from foilbench_py.types import LatticePopulation, MaskField, PointCloud, ScalarField, VelocityField


class LBMSolver:
    info = SolverInfo(
        id="lbm-d2q9",
        display_name="D2Q9 TRT LBM",
        dimensions=(2,),
        supports_moving_boundary=True,
        acceleration="vectorized NumPy",
    )

    _C = np.asarray(
        [[0, 0], [1, 0], [0, 1], [-1, 0], [0, -1], [1, 1], [-1, 1], [-1, -1], [1, -1]],
        dtype=np.int8,
    )
    _W = np.asarray([4 / 9, 1 / 9, 1 / 9, 1 / 9, 1 / 9, 1 / 36, 1 / 36, 1 / 36, 1 / 36])
    _OPPOSITE = np.asarray([0, 3, 4, 1, 2, 7, 8, 5, 6], dtype=np.int64)

    def __init__(self) -> None:
        self._scenario: Scenario | None = None
        self._geometry: NacaFoil | None = None
        self._f: LatticePopulation | None = None
        self._outlet: LatticePopulation | None = None
        self._solid: MaskField | None = None
        self._control = ControlState(0.0, 0.0, 0.0)
        self._time = 0.0
        self._density_initial = 1.0

    def _require(self) -> tuple[Scenario, NacaFoil, LatticePopulation, MaskField]:
        if (
            self._scenario is None
            or self._geometry is None
            or self._f is None
            or self._solid is None
        ):
            raise RuntimeError("solver has not been initialized")
        return self._scenario, self._geometry, self._f, self._solid

    def initialize(self, scenario: Scenario, geometry: NacaFoil, seed: int) -> None:
        del seed
        if scenario.domain.dimension != 2:
            raise NotImplementedError("D2Q9 Phase 1 supports only 2D")
        self._scenario = scenario
        self._geometry = geometry
        self._control = scenario.control_at(0.0)
        self._time = 0.0
        velocity = np.empty((scenario.domain.ny, scenario.domain.nx, 2), dtype=scenario.dtype)
        velocity[...] = np.asarray(scenario.freestream[:2], dtype=scenario.dtype) * 0.08
        initial = str(scenario.solver_options.get("initial_condition", "freestream"))
        positions = cell_centers(scenario.domain)
        if initial == "taylor-green":
            velocity[:, :, 0] = 0.08 * np.sin(positions[:, :, 0]) * np.cos(positions[:, :, 1])
            velocity[:, :, 1] = -0.08 * np.cos(positions[:, :, 0]) * np.sin(positions[:, :, 1])
        elif initial == "poiseuille":
            y0, y1 = scenario.domain.bounds[1]
            radius = 0.5 * (y1 - y0)
            center = 0.5 * (y0 + y1)
            velocity[:, :, 0] = 0.08 * 1.5 * (1.0 - ((positions[:, :, 1] - center) / radius) ** 2)
            velocity[:, :, 1] = 0.0
        density = np.ones((scenario.domain.ny, scenario.domain.nx), dtype=scenario.dtype)
        self._f = self._equilibrium(density, velocity)
        self._outlet = self._f[:, -1:, :].copy()
        self._solid = geometry.mask(scenario.domain, self._control.angle_degrees)

    def _equilibrium(
        self,
        density: ScalarField,
        velocity_lattice: VelocityField,
    ) -> LatticePopulation:
        c = self._C.astype(velocity_lattice.dtype)
        weights = self._W.astype(velocity_lattice.dtype)
        cu = einx.dot(
            "direction [component], ny nx [component] -> ny nx direction",
            c,
            velocity_lattice,
        )
        speed2 = einx.sum("ny nx [component] -> ny nx", velocity_lattice * velocity_lattice)
        return (
            density[:, :, None]
            * weights[None, None, :]
            * (1.0 + 3.0 * cu + 4.5 * cu * cu - 1.5 * speed2[:, :, None])
        )

    def _macroscopic(self, populations: LatticePopulation) -> tuple[ScalarField, VelocityField]:
        density = einx.sum("ny nx [direction] -> ny nx", populations)
        momentum = einx.dot(
            "ny nx [direction], [direction] component -> ny nx component",
            populations,
            self._C.astype(populations.dtype),
        )
        velocity = momentum / np.maximum(density[:, :, None], 1.0e-12)
        return density, velocity

    def _physical_velocity(self) -> VelocityField:
        scenario, _, populations, _ = self._require()
        _, lattice = self._macroscopic(populations)
        scale = max(np.linalg.norm(np.asarray(scenario.freestream[:2])), 1.0e-12) / 0.08
        return lattice * scale

    def _update_solid(self, control: ControlState) -> None:
        scenario, geometry, populations, old_solid = self._require()
        new_solid = geometry.mask(scenario.domain, control.angle_degrees)
        uncovered = old_solid & ~new_solid
        if np.any(uncovered):
            density, velocity = self._macroscopic(populations)
            density[uncovered] = 1.0
            velocity[uncovered] = np.asarray(scenario.freestream[:2]) * 0.08
            populations[uncovered] = self._equilibrium(density, velocity)[uncovered]
        self._solid = new_solid

    def _stream_with_moving_wall(
        self,
        post_collision: LatticePopulation,
        density: ScalarField,
        control: ControlState,
        lattice_velocity_scale: float,
    ) -> LatticePopulation:
        """Stream fluid links and apply Bouzidi-style interpolated wall reflection."""
        scenario, geometry, _, solid = self._require()
        centers = cell_centers(scenario.domain)
        signed_distance = geometry.signed_distance(
            centers.reshape(-1, 2), control.angle_degrees
        ).reshape(scenario.domain.ny, scenario.domain.nx)
        streamed = np.zeros_like(post_collision)
        for direction, (cx_raw, cy_raw) in enumerate(self._C):
            cx = int(cx_raw)
            cy = int(cy_raw)
            if direction == 0:
                streamed[:, :, 0] = post_collision[:, :, 0]
                continue

            destination_solid = np.roll(solid, shift=(-cy, -cx), axis=(0, 1))
            wall_link = ~solid & destination_solid
            outgoing = np.where(
                (~solid & ~destination_solid)[:, :, None],
                post_collision[:, :, direction : direction + 1],
                0.0,
            )[:, :, 0]
            streamed[:, :, direction] += np.roll(outgoing, shift=(cy, cx), axis=(0, 1))
            if not np.any(wall_link):
                continue

            destination_distance = np.roll(signed_distance, shift=(-cy, -cx), axis=(0, 1))
            link_fraction = np.clip(
                signed_distance / np.maximum(signed_distance - destination_distance, 1.0e-12),
                0.05,
                1.0,
            )
            q = link_fraction[wall_link]
            source_population = post_collision[:, :, direction][wall_link]
            opposite = int(self._OPPOSITE[direction])
            reflected = np.empty_like(source_population)
            near = q < 0.5
            if np.any(near):
                upstream = np.roll(post_collision[:, :, direction], shift=(cy, cx), axis=(0, 1))[
                    wall_link
                ]
                reflected[near] = (
                    2.0 * q[near] * source_population[near] + (1.0 - 2.0 * q[near]) * upstream[near]
                )
            if np.any(~near):
                far_q = q[~near]
                reflected[~near] = source_population[~near] / (2.0 * far_q) + (
                    2.0 * far_q - 1.0
                ) * post_collision[:, :, opposite][wall_link][~near] / (2.0 * far_q)

            wall_points = centers[wall_link].copy()
            wall_points[:, 0] += q * cx * scenario.domain.dx
            wall_points[:, 1] += q * cy * scenario.domain.dy
            wall_velocity = geometry.wall_velocity(wall_points, control) * lattice_velocity_scale
            wall_projection = cx * wall_velocity[:, 0] + cy * wall_velocity[:, 1]
            reflected -= 6.0 * self._W[direction] * density[wall_link] * wall_projection
            streamed[:, :, opposite][wall_link] = reflected
        return streamed

    def _step(self, dt: float, control: ControlState) -> None:
        scenario, _, populations, _ = self._require()
        dx = scenario.domain.dx
        freestream_speed = max(abs(scenario.freestream[0]), 1.0e-12)
        u_lattice = min(freestream_speed * dt / dx, 0.08)
        chord_cells = scenario.foil.chord / dx
        nu_lattice = max(u_lattice * chord_cells / scenario.reynolds, 1.0e-5)
        tau_plus = 0.5 + 3.0 * nu_lattice
        tau_minus = 0.5 + (3.0 / 16.0) / max(tau_plus - 0.5, 1.0e-6)
        omega_plus = 1.0 / tau_plus
        omega_minus = 1.0 / tau_minus

        density, velocity = self._macroscopic(populations)
        target = np.asarray(scenario.freestream[:2], dtype=populations.dtype)
        target_lattice = target * (u_lattice / freestream_speed)
        equilibrium = self._equilibrium(density, velocity)
        opposite_f = populations[:, :, self._OPPOSITE]
        opposite_eq = equilibrium[:, :, self._OPPOSITE]
        even = 0.5 * (populations + opposite_f)
        odd = 0.5 * (populations - opposite_f)
        even_eq = 0.5 * (equilibrium + opposite_eq)
        odd_eq = 0.5 * (equilibrium - opposite_eq)
        post = populations - omega_plus * (even - even_eq) - omega_minus * (odd - odd_eq)

        streamed = self._stream_with_moving_wall(
            post,
            density,
            control,
            u_lattice / freestream_speed,
        )

        inlet_eq = self._equilibrium(
            np.ones_like(density), np.broadcast_to(target_lattice, velocity.shape).copy()
        )
        if "x" not in scenario.domain.periodic_axes:
            streamed[:, 0, :] = inlet_eq[:, 0, :]
            previous_outlet = (
                populations[:, -1, :] if self._outlet is None else self._outlet[:, 0, :]
            )
            streamed[:, -1, :] = previous_outlet + u_lattice * (
                streamed[:, -2, :] - previous_outlet
            )
            self._outlet = streamed[:, -1:, :].copy()
        if "y" in scenario.domain.periodic_axes:
            pass
        elif str(scenario.solver_options.get("initial_condition", "")) == "poiseuille":
            streamed[0, :, :] = streamed[1, :, :][:, self._OPPOSITE]
            streamed[-1, :, :] = streamed[-2, :, :][:, self._OPPOSITE]
        else:
            streamed[0, :, :] = inlet_eq[0, :, :]
            streamed[-1, :, :] = inlet_eq[-1, :, :]
        self._f = streamed
        self._control = control

    def advance(self, control: ControlState, target_dt: float) -> StepReport:
        scenario, _, _, _ = self._require()
        if target_dt <= 0.0:
            raise ValueError("target_dt must be positive")
        dt_max = 0.08 * scenario.domain.dx / max(abs(scenario.freestream[0]), 1.0e-6)
        substeps = max(1, int(np.ceil(target_dt / dt_max)))
        dt = target_dt / substeps
        for substep in range(substeps):
            fraction = (substep + 1) / substeps
            sub_control = ControlState(
                self._time + fraction * target_dt,
                self._control.angle_degrees
                + fraction * (control.angle_degrees - self._control.angle_degrees),
                control.angular_velocity_degrees,
            )
            self._update_solid(sub_control)
            self._step(dt, sub_control)
        self._time += target_dt
        self._control = ControlState(
            self._time, control.angle_degrees, control.angular_velocity_degrees
        )
        max_speed = float(np.max(np.linalg.norm(self._physical_velocity(), axis=2)))
        return StepReport(target_dt, target_dt, substeps, max_speed)

    def sample_velocity(self, points: PointCloud) -> PointCloud:
        scenario, _, _, _ = self._require()
        return sample_vector(self._physical_velocity(), points, scenario.domain)

    def export_state(self) -> CanonicalFlowState:
        scenario, _, populations, _ = self._require()
        density, _ = self._macroscopic(populations)
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
            velocity=self._physical_velocity()[None, ...],
            density=density[None, ...],
        )

    def import_state(self, state: CanonicalFlowState, control: ControlState) -> ImportReport:
        scenario, geometry, _, _ = self._require()
        if state.dimension != 2 or state.resolution != scenario.domain.resolution:
            raise ValueError("warm import requires the same 2D resolution")
        physical = np.asarray(state.velocity[0], dtype=scenario.dtype)
        speed = max(abs(scenario.freestream[0]), 1.0e-12)
        lattice = physical * (0.08 / speed)
        density = (
            np.ones((scenario.domain.ny, scenario.domain.nx), dtype=scenario.dtype)
            if state.density is None
            else np.asarray(state.density[0], dtype=scenario.dtype)
        )
        self._f = self._equilibrium(density, lattice)
        self._outlet = self._f[:, -1:, :].copy()
        self._time = state.time
        self._control = control
        self._solid = geometry.mask(scenario.domain, control.angle_degrees)
        return ImportReport(
            state.source_solver,
            self.info.id,
            ("non-equilibrium lattice populations", "TRT kinetic modes"),
            ("LBM resumes from local equilibrium; an initialization transient is expected.",),
        )

    def diagnostics(self) -> Diagnostics:
        scenario, _, populations, solid = self._require()
        density, _ = self._macroscopic(populations)
        velocity = self._physical_velocity()
        values = {
            "time": self._time,
            "kinetic_energy": kinetic_energy(velocity),
            "enstrophy": enstrophy(velocity, scenario.domain),
            "divergence_l2": divergence_l2(velocity, scenario.domain),
            "solid_leakage": solid_leakage(velocity, solid),
            "density_mean": float(np.mean(density)),
            "density_drift": float(np.mean(density) - self._density_initial),
            "wake_width": wake_width(velocity, scenario.domain, scenario.foil.pivot[0]),
            "recirculation_area": recirculation_area(
                velocity, scenario.domain, scenario.foil.pivot[0]
            ),
        }
        if not all(np.isfinite(value) for value in values.values()):
            raise FloatingPointError("LBM produced non-finite diagnostics")
        return Diagnostics(values)
