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
from foilbench_py.solvers._numba_adapter import lbm_trt_collision
from foilbench_py.types import (
    CoordinateField,
    LatticePopulation,
    MaskField,
    PointCloud,
    ScalarField,
    VelocityField,
)


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
        self._sponge: ScalarField | None = None
        self._solid: MaskField | None = None
        self._centers: CoordinateField | None = None
        self._signed_distance: ScalarField | None = None
        self._solid_angle: float | None = None
        self._boundary_equilibrium: LatticePopulation | None = None
        self._control = ControlState(0.0, 0.0, 0.0)
        self._time = 0.0
        self._density_initial = 1.0
        self._lattice_speed = 0.08
        self._lattice_dt = 1.0
        self._effective_reynolds = 0.0
        self._viscosity_clamped = False

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
        freestream_speed = max(abs(scenario.freestream[0]), 1.0e-12)
        reference_substeps = max(
            1,
            int(np.ceil(scenario.output_dt * freestream_speed / (0.08 * scenario.domain.dx))),
        )
        self._lattice_dt = scenario.output_dt / reference_substeps
        self._lattice_speed = freestream_speed * self._lattice_dt / scenario.domain.dx
        chord_cells = scenario.foil.chord / scenario.domain.dx
        requested_viscosity = self._lattice_speed * chord_cells / scenario.reynolds
        minimum_preview_viscosity = (0.52 - 0.5) / 3.0
        selected_viscosity = max(
            requested_viscosity,
            minimum_preview_viscosity,
        )
        self._viscosity_clamped = selected_viscosity > requested_viscosity
        self._effective_reynolds = self._lattice_speed * chord_cells / selected_viscosity
        velocity = np.empty((scenario.domain.ny, scenario.domain.nx, 2), dtype=scenario.dtype)
        velocity[...] = np.asarray(scenario.freestream[:2], dtype=scenario.dtype) * (
            self._lattice_speed / freestream_speed
        )
        initial = str(scenario.solver_options.get("initial_condition", "freestream"))
        positions = cell_centers(scenario.domain)
        self._centers = positions
        if initial == "taylor-green":
            velocity[:, :, 0] = (
                self._lattice_speed * np.sin(positions[:, :, 0]) * np.cos(positions[:, :, 1])
            )
            velocity[:, :, 1] = (
                -self._lattice_speed * np.cos(positions[:, :, 0]) * np.sin(positions[:, :, 1])
            )
        elif initial == "poiseuille":
            y0, y1 = scenario.domain.bounds[1]
            radius = 0.5 * (y1 - y0)
            center = 0.5 * (y0 + y1)
            velocity[:, :, 0] = (
                self._lattice_speed * 1.5 * (1.0 - ((positions[:, :, 1] - center) / radius) ** 2)
            )
            velocity[:, :, 1] = 0.0
        density = np.ones((scenario.domain.ny, scenario.domain.nx), dtype=scenario.dtype)
        self._f = self._equilibrium(density, velocity)
        self._outlet = self._f[:, -1:, :].copy()
        self._signed_distance = geometry.signed_distance(
            positions.reshape(-1, 2),
            self._control.angle_degrees,
        ).reshape(scenario.domain.ny, scenario.domain.nx)
        self._solid = self._signed_distance <= 0.0
        self._solid_angle = self._control.angle_degrees
        target_lattice = np.asarray(scenario.freestream[:2], dtype=scenario.dtype) * (
            self._lattice_speed / freestream_speed
        )
        target_velocity = np.empty_like(velocity)
        target_velocity[...] = target_lattice
        self._boundary_equilibrium = self._equilibrium(
            np.ones_like(density),
            target_velocity,
        )
        sponge = np.zeros((scenario.domain.ny, scenario.domain.nx), dtype=scenario.dtype)
        width = max(3, min(scenario.domain.nx, scenario.domain.ny) // 16)
        if "y" not in scenario.domain.periodic_axes:
            y = np.arange(scenario.domain.ny)
            distance_y = np.minimum(y, scenario.domain.ny - 1 - y)
            strength_y = 0.12 * np.clip((width - distance_y) / width, 0.0, 1.0) ** 2
            sponge = np.maximum(sponge, strength_y[:, None])
        if "x" not in scenario.domain.periodic_axes:
            outlet_width = 2 * width
            distance_outlet = scenario.domain.nx - 1 - np.arange(scenario.domain.nx)
            strength_outlet = (
                0.08
                * np.clip(
                    (outlet_width - distance_outlet) / outlet_width,
                    0.0,
                    1.0,
                )
                ** 2
            )
            sponge = np.maximum(sponge, strength_outlet[None, :])
        self._sponge = sponge

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
        scale = (
            max(np.linalg.norm(np.asarray(scenario.freestream[:2])), 1.0e-12) / self._lattice_speed
        )
        return lattice * scale

    def _update_solid(self, control: ControlState) -> None:
        scenario, geometry, populations, old_solid = self._require()
        if self._solid_angle == control.angle_degrees:
            return
        if self._centers is None:
            raise RuntimeError("LBM geometry cache has not been initialized")
        signed_distance = geometry.signed_distance(
            self._centers.reshape(-1, 2),
            control.angle_degrees,
        ).reshape(scenario.domain.ny, scenario.domain.nx)
        new_solid = signed_distance <= 0.0
        uncovered = old_solid & ~new_solid
        if np.any(uncovered):
            density, velocity = self._macroscopic(populations)
            density[uncovered] = 1.0
            speed = max(abs(scenario.freestream[0]), 1.0e-12)
            velocity[uncovered] = np.asarray(scenario.freestream[:2]) * (
                self._lattice_speed / speed
            )
            populations[uncovered] = self._equilibrium(density, velocity)[uncovered]
        self._solid = new_solid
        self._signed_distance = signed_distance
        self._solid_angle = control.angle_degrees

    @staticmethod
    def _left_velocity_boundary(
        populations: LatticePopulation,
        ux: float,
        uy: float,
    ) -> None:
        boundary = populations[:, 0, :]
        density = (
            boundary[:, 0]
            + boundary[:, 2]
            + boundary[:, 4]
            + 2.0 * (boundary[:, 3] + boundary[:, 6] + boundary[:, 7])
        ) / (1.0 - ux)
        boundary[:, 1] = boundary[:, 3] + (2.0 / 3.0) * density * ux
        boundary[:, 5] = (
            boundary[:, 7]
            + 0.5 * (boundary[:, 4] - boundary[:, 2])
            + (1.0 / 6.0) * density * ux
            + 0.5 * density * uy
        )
        boundary[:, 8] = (
            boundary[:, 6]
            + 0.5 * (boundary[:, 2] - boundary[:, 4])
            + (1.0 / 6.0) * density * ux
            - 0.5 * density * uy
        )

    @staticmethod
    def _bottom_velocity_boundary(
        populations: LatticePopulation,
        ux: float,
        uy: float,
    ) -> None:
        boundary = populations[0, :, :]
        density = (
            boundary[:, 0]
            + boundary[:, 1]
            + boundary[:, 3]
            + 2.0 * (boundary[:, 4] + boundary[:, 7] + boundary[:, 8])
        ) / (1.0 - uy)
        boundary[:, 2] = boundary[:, 4] + (2.0 / 3.0) * density * uy
        boundary[:, 5] = (
            boundary[:, 7]
            + 0.5 * (boundary[:, 3] - boundary[:, 1])
            + (1.0 / 6.0) * density * uy
            + 0.5 * density * ux
        )
        boundary[:, 6] = (
            boundary[:, 8]
            + 0.5 * (boundary[:, 1] - boundary[:, 3])
            + (1.0 / 6.0) * density * uy
            - 0.5 * density * ux
        )

    @staticmethod
    def _top_velocity_boundary(
        populations: LatticePopulation,
        ux: float,
        uy: float,
    ) -> None:
        boundary = populations[-1, :, :]
        density = (
            boundary[:, 0]
            + boundary[:, 1]
            + boundary[:, 3]
            + 2.0 * (boundary[:, 2] + boundary[:, 5] + boundary[:, 6])
        ) / (1.0 + uy)
        boundary[:, 4] = boundary[:, 2] - (2.0 / 3.0) * density * uy
        boundary[:, 7] = (
            boundary[:, 5]
            + 0.5 * (boundary[:, 1] - boundary[:, 3])
            - (1.0 / 6.0) * density * uy
            - 0.5 * density * ux
        )
        boundary[:, 8] = (
            boundary[:, 6]
            + 0.5 * (boundary[:, 3] - boundary[:, 1])
            - (1.0 / 6.0) * density * uy
            + 0.5 * density * ux
        )

    def _stream_with_moving_wall(
        self,
        post_collision: LatticePopulation,
        density: ScalarField,
        control: ControlState,
        lattice_velocity_scale: float,
    ) -> LatticePopulation:
        """Stream fluid links and apply Bouzidi-style interpolated wall reflection."""
        scenario, geometry, _, solid = self._require()
        centers = self._centers
        signed_distance = self._signed_distance
        if centers is None or signed_distance is None:
            raise RuntimeError("LBM geometry cache has not been initialized")
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
        del dt
        u_lattice = self._lattice_speed
        chord_cells = scenario.foil.chord / dx
        minimum_preview_viscosity = (0.52 - 0.5) / 3.0
        requested_viscosity = u_lattice * chord_cells / scenario.reynolds
        nu_lattice = max(requested_viscosity, minimum_preview_viscosity)
        self._viscosity_clamped = nu_lattice > requested_viscosity
        self._effective_reynolds = u_lattice * chord_cells / nu_lattice
        tau_plus = 0.5 + 3.0 * nu_lattice
        tau_minus = 0.5 + (3.0 / 16.0) / max(tau_plus - 0.5, 1.0e-6)
        omega_plus = 1.0 / tau_plus
        omega_minus = 1.0 / tau_minus

        density, post = lbm_trt_collision(populations, omega_plus, omega_minus)
        target = np.asarray(scenario.freestream[:2], dtype=populations.dtype)
        target_lattice = target * (u_lattice / freestream_speed)

        streamed = self._stream_with_moving_wall(
            post,
            density,
            control,
            u_lattice / freestream_speed,
        )

        boundary_equilibrium = self._boundary_equilibrium
        if boundary_equilibrium is None:
            raise RuntimeError("LBM boundary cache has not been initialized")
        if "y" in scenario.domain.periodic_axes:
            pass
        elif str(scenario.solver_options.get("initial_condition", "")) == "poiseuille":
            streamed[0, :, :] = streamed[1, :, :][:, self._OPPOSITE]
            streamed[-1, :, :] = streamed[-2, :, :][:, self._OPPOSITE]
        else:
            self._bottom_velocity_boundary(
                streamed,
                float(target_lattice[0]),
                float(target_lattice[1]),
            )
            self._top_velocity_boundary(
                streamed,
                float(target_lattice[0]),
                float(target_lattice[1]),
            )
        if "x" not in scenario.domain.periodic_axes:
            self._left_velocity_boundary(
                streamed,
                float(target_lattice[0]),
                float(target_lattice[1]),
            )
            previous_outlet = (
                populations[:, -1, :] if self._outlet is None else self._outlet[:, 0, :]
            )
            streamed[:, -1, :] = previous_outlet + u_lattice * (
                streamed[:, -2, :] - previous_outlet
            )
            self._outlet = streamed[:, -1:, :].copy()
        if "x" not in scenario.domain.periodic_axes and "y" not in scenario.domain.periodic_axes:
            streamed[0, 0, :] = boundary_equilibrium[0, 0, :]
            streamed[-1, 0, :] = boundary_equilibrium[-1, 0, :]
            streamed[0, -1, :] = boundary_equilibrium[0, -1, :]
            streamed[-1, -1, :] = boundary_equilibrium[-1, -1, :]
        if self._sponge is not None:
            strength = self._sponge[:, :, None]
            streamed = (1.0 - strength) * streamed + strength * boundary_equilibrium
        self._f = streamed
        self._control = control

    def advance(self, control: ControlState, target_dt: float) -> StepReport:
        self._require()
        if target_dt <= 0.0:
            raise ValueError("target_dt must be positive")
        substeps = max(1, int(np.ceil(target_dt / self._lattice_dt - 1.0e-12)))
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
        warnings = (
            (f"LBM relaxation clamp active: effective Re={self._effective_reynolds:.1f}",)
            if self._viscosity_clamped
            else ()
        )
        return StepReport(target_dt, target_dt, substeps, max_speed, warnings)

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
        lattice = physical * (self._lattice_speed / speed)
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
        if self._centers is None:
            raise RuntimeError("LBM geometry cache has not been initialized")
        self._signed_distance = geometry.signed_distance(
            self._centers.reshape(-1, 2),
            control.angle_degrees,
        ).reshape(scenario.domain.ny, scenario.domain.nx)
        self._solid_angle = control.angle_degrees
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
            "effective_reynolds": self._effective_reynolds,
            "wake_width": wake_width(velocity, scenario.domain, scenario.foil.pivot[0]),
            "recirculation_area": recirculation_area(
                velocity, scenario.domain, scenario.foil.pivot[0]
            ),
        }
        if not all(np.isfinite(value) for value in values.values()):
            raise FloatingPointError("LBM produced non-finite diagnostics")
        warnings = (
            (f"LBM relaxation clamp active: effective Re={self._effective_reynolds:.1f}",)
            if self._viscosity_clamped
            else ()
        )
        return Diagnostics(values, warnings)
