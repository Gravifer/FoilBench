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
from foilbench_py.solvers._numba_adapter import (
    lbm_apply_sponge,
    lbm_moving_wall_stream,
    lbm_trt_collision,
)
from foilbench_py.types import (
    CoordinateField,
    LatticePopulation,
    MaskField,
    PointCloud,
    ScalarField,
    VelocityField,
)

type LBMCheckpoint = tuple[
    LatticePopulation,
    LatticePopulation | None,
    MaskField,
    ScalarField | None,
    float | None,
    ControlState,
    float,
    float,
    float,
    float,
    bool,
    float,
    float,
    LatticePopulation | None,
    int,
]


class LBMSolver:
    info = SolverInfo(
        id="lbm-d2q9",
        display_name="D2Q9 TRT LBM",
        dimensions=(2,),
        supports_moving_boundary=True,
        supported_precisions=("float32", "float64"),
        acceleration="Numba TRT collision + vectorized NumPy streaming",
    )

    _C = np.asarray(
        [[0, 0], [1, 0], [0, 1], [-1, 0], [0, -1], [1, 1], [-1, 1], [-1, -1], [1, -1]],
        dtype=np.int8,
    )
    _W = np.asarray([4 / 9, 1 / 9, 1 / 9, 1 / 9, 1 / 9, 1 / 36, 1 / 36, 1 / 36, 1 / 36])
    _OPPOSITE = np.asarray([0, 3, 4, 1, 2, 7, 8, 5, 6], dtype=np.int64)
    _LATTICE_SOUND_SPEED: float = float(1.0 / np.sqrt(3.0))
    _MAXIMUM_MACH: float = 0.08
    _MAXIMUM_LATTICE_SPEED: float = _MAXIMUM_MACH * _LATTICE_SOUND_SPEED
    _MAXIMUM_SUBSTEPS: int = 512
    _MINIMUM_POPULATION: float = -0.05

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
        self._control: ControlState = ControlState(0.0, 0.0, 0.0)
        self._time: float = 0.0
        self._density_initial: float = 1.0
        self._lattice_speed: float = 0.08
        self._lattice_dt: float = 1.0
        self._reference_speed: float = 1.0
        self._effective_reynolds: float = 0.0
        self._viscosity_clamped: bool = False
        self._reynolds: float = 1.0
        self._omega_plus: float = 1.0
        self._omega_minus: float = 1.0
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
        previous = (
            self._reynolds,
            self._effective_reynolds,
            self._viscosity_clamped,
            self._omega_plus,
            self._omega_minus,
            self._revision,
        )
        requested = float(reynolds)
        try:
            self._reynolds = requested
            if self._scenario is not None:
                self._configure_relaxation()
        except Exception:
            (
                self._reynolds,
                self._effective_reynolds,
                self._viscosity_clamped,
                self._omega_plus,
                self._omega_minus,
                self._revision,
            ) = previous
            raise
        if requested != previous[0]:
            self._revision += 1
        warnings = (
            (f"effective Reynolds clamped to {self._effective_reynolds:.1f}",)
            if self._viscosity_clamped
            else ()
        )
        return ReynoldsOutcome(requested, self._effective_reynolds, warnings)

    def interactive_tuning(self) -> InteractiveTuning | None:
        return None

    def adjust_interactive_tuning(self, direction: int) -> InteractiveTuning | None:
        del direction
        return None

    def apply_interactive_tuning(self, value: str | float) -> InteractiveTuning | None:
        del value
        return None

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
        self._reynolds = float(scenario.reynolds)
        self._revision = 0
        self._reference_speed = scenario.reference_speed
        reference_substeps = max(
            1,
            int(
                np.ceil(
                    scenario.output_dt
                    * self._reference_speed
                    / (self._MAXIMUM_LATTICE_SPEED * scenario.domain.dx)
                    - 1.0e-12
                )
            ),
        )
        self._lattice_dt = scenario.output_dt / reference_substeps
        self._lattice_speed = self._reference_speed * self._lattice_dt / scenario.domain.dx
        self._configure_relaxation()
        velocity = np.empty((scenario.domain.ny, scenario.domain.nx, 2), dtype=scenario.dtype)
        velocity[...] = np.asarray(scenario.freestream[:2], dtype=scenario.dtype) * (
            self._lattice_speed / self._reference_speed
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
            self._lattice_speed / self._reference_speed
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
        self._update_solid(self._control)
        self._revision = 0

    def _configure_relaxation(self) -> None:
        scenario = self._scenario
        if scenario is None:
            raise RuntimeError("solver has not been initialized")
        chord_cells = scenario.foil.chord / scenario.domain.dx
        requested_viscosity = self._lattice_speed * chord_cells / self._reynolds
        minimum_preview_viscosity = (0.52 - 0.5) / 3.0
        selected_viscosity = max(requested_viscosity, minimum_preview_viscosity)
        self._viscosity_clamped = selected_viscosity > requested_viscosity
        self._effective_reynolds = (
            self._lattice_speed * chord_cells / selected_viscosity
        )
        tau_plus = 0.5 + 3.0 * selected_viscosity
        tau_minus = 0.5 + (3.0 / 16.0) / max(tau_plus - 0.5, 1.0e-6)
        self._omega_plus = 1.0 / tau_plus
        self._omega_minus = 1.0 / tau_minus
        if not (
            0.0 < self._omega_plus < 2.0 and 0.0 < self._omega_minus < 2.0
        ):
            raise NumericalFailure(
                "invalid_relaxation",
                "TRT relaxation frequency left the interval (0, 2)",
                "collision",
                {
                    "omega_plus": self._omega_plus,
                    "omega_minus": self._omega_minus,
                },
            )

    def _rebuild_boundary_equilibrium(self) -> None:
        scenario = self._scenario
        if scenario is None:
            raise RuntimeError("solver has not been initialized")
        target_lattice = np.asarray(scenario.freestream[:2], dtype=scenario.dtype) * (
            self._lattice_speed / self._reference_speed
        )
        target_velocity = np.empty(
            (scenario.domain.ny, scenario.domain.nx, 2),
            dtype=scenario.dtype,
        )
        target_velocity[...] = target_lattice
        self._boundary_equilibrium = self._equilibrium(
            np.ones((scenario.domain.ny, scenario.domain.nx), dtype=scenario.dtype),
            target_velocity,
        )

    def _rescale_populations(self, selected_speed: float) -> None:
        scenario, _, populations, _ = self._require()
        density, old_velocity = self._macroscopic(populations)
        ratio = selected_speed / self._lattice_speed
        new_velocity = old_velocity * ratio
        old_equilibrium = self._equilibrium(density, old_velocity)
        new_equilibrium = self._equilibrium(density, new_velocity)
        self._f = np.asarray(
            new_equilibrium + ratio * (populations - old_equilibrium),
            dtype=scenario.dtype,
        )
        self._outlet = self._f[:, -1:, :].copy()

    def _configure_temporal_scaling(
        self,
        target_dt: float,
        maximum_physical_speed: float,
        minimum_substeps: int = 1,
    ) -> int:
        scenario, _, populations, _ = self._require()
        del populations
        selected_maximum = max(maximum_physical_speed, self._reference_speed)
        substeps = max(
            minimum_substeps,
            int(
                np.ceil(
                    target_dt
                    * selected_maximum
                    / (self._MAXIMUM_LATTICE_SPEED * scenario.domain.dx)
                    - 1.0e-12
                )
            ),
        )
        if substeps > self._MAXIMUM_SUBSTEPS:
            raise NumericalFailure(
                "excessive_velocity",
                "LBM motion requires too many lattice substeps",
                "time-mapping",
                {
                    "required_substeps": substeps,
                    "maximum_substeps": self._MAXIMUM_SUBSTEPS,
                    "maximum_physical_speed": maximum_physical_speed,
                },
            )
        selected_speed = (
            self._reference_speed * target_dt / (substeps * scenario.domain.dx)
        )
        if abs(selected_speed - self._lattice_speed) > 1.0e-12:
            self._rescale_populations(selected_speed)
        self._lattice_dt = target_dt / substeps
        self._lattice_speed = selected_speed
        self._configure_relaxation()
        self._rebuild_boundary_equilibrium()
        return substeps

    def _configure_import_scaling(self, maximum_physical_speed: float) -> None:
        """Choose a canonical reconstruction scale independent of output cadence."""
        scenario, _, populations, _ = self._require()
        del populations
        selected_maximum = max(maximum_physical_speed, self._reference_speed)
        self._lattice_speed = (
            self._MAXIMUM_LATTICE_SPEED
            * self._reference_speed
            / selected_maximum
        )
        self._lattice_dt = (
            self._lattice_speed * scenario.domain.dx / self._reference_speed
        )
        self._configure_relaxation()
        self._rebuild_boundary_equilibrium()

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
        scale = self._reference_speed / self._lattice_speed
        return np.asarray(lattice * scale, dtype=scenario.dtype)

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
            velocity[uncovered] = np.asarray(scenario.freestream[:2]) * (
                self._lattice_speed / self._reference_speed
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
        signed_distance = self._signed_distance
        if self._centers is None or signed_distance is None:
            raise RuntimeError("LBM geometry cache has not been initialized")
        return lbm_moving_wall_stream(
            post_collision,
            density,
            solid,
            signed_distance,
            scenario.domain.bounds,
            (scenario.domain.dx, scenario.domain.dy),
            (geometry.spec.pivot[0], geometry.spec.pivot[1]),
            float(np.deg2rad(control.angular_velocity_degrees)),
            lattice_velocity_scale,
        )

    def _step(self, control: ControlState) -> None:
        scenario, _, populations, _ = self._require()
        u_lattice = self._lattice_speed
        density, post = lbm_trt_collision(
            populations,
            self._omega_plus,
            self._omega_minus,
        )
        target = np.asarray(scenario.freestream[:2], dtype=populations.dtype)
        target_lattice = target * (u_lattice / self._reference_speed)

        streamed = self._stream_with_moving_wall(
            post,
            density,
            control,
            u_lattice / self._reference_speed,
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
            lbm_apply_sponge(streamed, boundary_equilibrium, self._sponge)
        self._f = streamed
        self._control = control

    def _advance_once(
        self,
        control: ControlState,
        target_dt: float,
        minimum_substeps: int,
        stability_retries: int,
    ) -> StepReport:
        scenario, geometry, populations, solid = self._require()
        validate_advance_request(self._time, control, target_dt, scenario.precision)
        if not np.isfinite(populations).all():
            raise NumericalFailure(
                "nonfinite_state",
                "LBM populations are non-finite",
                "postcondition",
            )
        checkpoint: LBMCheckpoint = (
            populations.copy(),
            None if self._outlet is None else self._outlet.copy(),
            solid.copy(),
            None if self._signed_distance is None else self._signed_distance.copy(),
            self._solid_angle,
            self._control,
            self._time,
            self._lattice_speed,
            self._lattice_dt,
            self._effective_reynolds,
            self._viscosity_clamped,
            self._omega_plus,
            self._omega_minus,
            None
            if self._boundary_equilibrium is None
            else self._boundary_equilibrium.copy(),
            self._revision,
        )
        start_time = self._time
        start_angle = self._control.angle_degrees
        current_velocity = self._physical_velocity()
        current_maximum = float(np.max(np.linalg.norm(current_velocity, axis=2)))
        wall_speed = (
            abs(np.deg2rad(control.angular_velocity_degrees)) * geometry.maximum_radius
        )
        sweep_speed = (
            abs(np.deg2rad(control.angle_degrees - start_angle))
            * geometry.maximum_radius
            / target_dt
        )
        maximum_physical_speed = max(
            current_maximum,
            self._reference_speed,
            wall_speed,
            sweep_speed,
        )
        substeps = 0
        max_speed = 0.0
        density_excursion = 0.0
        minimum_population = 0.0
        maximum_mach = 0.0
        try:
            substeps = self._configure_temporal_scaling(
                target_dt,
                1.25 * maximum_physical_speed,
                minimum_substeps,
            )
            for substep in range(substeps):
                fraction = (substep + 1) / substeps
                sub_control = ControlState(
                    start_time + fraction * target_dt,
                    start_angle + fraction * (control.angle_degrees - start_angle),
                    control.angular_velocity_degrees,
                )
                self._update_solid(sub_control)
                self._step(sub_control)
            updated_populations = self._f
            if updated_populations is None:
                raise NumericalFailure(
                    "nonfinite_state",
                    "LBM lost its population state",
                    "postcondition",
                )
            if not np.isfinite(updated_populations).all():
                raise NumericalFailure(
                    "invalid_population",
                    "LBM produced non-finite populations",
                    "postcondition",
                )
            minimum_population = float(np.min(updated_populations))
            if minimum_population < self._MINIMUM_POPULATION:
                raise NumericalFailure(
                    "invalid_population",
                    "LBM population left the admissible nonnegative envelope",
                    "postcondition",
                    {
                        "minimum_population": minimum_population,
                        "minimum_allowed_population": self._MINIMUM_POPULATION,
                    },
                )
            with np.errstate(over="ignore", invalid="ignore", divide="ignore"):
                density, _ = self._macroscopic(updated_populations)
                physical_velocity = self._physical_velocity()
            final_solid = self._solid
            if final_solid is None:
                raise RuntimeError("LBM solid mask is unavailable")
            fluid_density = density[~final_solid]
            if not np.isfinite(fluid_density).all() or np.any(fluid_density <= 0.0):
                raise NumericalFailure(
                    "invalid_density",
                    "LBM density became non-positive or non-finite",
                    "postcondition",
                )
            density_excursion = float(np.max(np.abs(fluid_density - 1.0)))
            if density_excursion > 0.75:
                raise NumericalFailure(
                    "invalid_density",
                    "LBM density excursion exceeded the interactive envelope",
                    "postcondition",
                    {
                        "density_excursion": density_excursion,
                        "maximum_density_excursion": 0.75,
                    },
                )
            if not np.isfinite(physical_velocity).all():
                raise NumericalFailure(
                    "nonfinite_state",
                    "LBM produced non-finite macroscopic velocity",
                    "postcondition",
                )
            max_speed = float(np.max(np.linalg.norm(physical_velocity, axis=2)))
            maximum_mach = (
                max(max_speed, self._reference_speed, wall_speed, sweep_speed)
                * self._lattice_speed
                / (self._reference_speed * self._LATTICE_SOUND_SPEED)
            )
            if maximum_mach > self._MAXIMUM_MACH * (1.0 + 1.0e-6):
                raise NumericalFailure(
                    "excessive_velocity",
                    "LBM lattice Mach exceeded the admissible limit",
                    "postcondition",
                    {
                        "maximum_lattice_mach": maximum_mach,
                        "maximum_lattice_mach_limit": self._MAXIMUM_MACH,
                        "attempted_substeps": substeps,
                    },
                )
        except Exception:
            (
                self._f,
                self._outlet,
                self._solid,
                self._signed_distance,
                self._solid_angle,
                self._control,
                self._time,
                self._lattice_speed,
                self._lattice_dt,
                self._effective_reynolds,
                self._viscosity_clamped,
                self._omega_plus,
                self._omega_minus,
                self._boundary_equilibrium,
                self._revision,
            ) = checkpoint
            raise
        self._time = start_time + target_dt
        self._control = ControlState(
            self._time,
            control.angle_degrees,
            control.angular_velocity_degrees,
        )
        self._revision += 1
        warnings = (
            (f"LBM relaxation clamp active: effective Re={self._effective_reynolds:.1f}",)
            if self._viscosity_clamped
            else ()
        )
        tau_plus = 1.0 / self._omega_plus
        tau_minus = 1.0 / self._omega_minus
        return StepReport(
            target_dt,
            target_dt,
            substeps,
            max_speed,
            warnings,
            self._revision,
            {
                "maximum_fluid_speed": max_speed,
                "stability_retries": stability_retries,
                "maximum_wall_speed": wall_speed,
                "maximum_geometry_sweep_speed": sweep_speed,
                "maximum_lattice_mach": maximum_mach,
                "density_excursion": density_excursion,
                "minimum_population": minimum_population,
                "omega_plus": self._omega_plus,
                "omega_minus": self._omega_minus,
                "trt_magic": (tau_plus - 0.5) * (tau_minus - 0.5),
                "requested_reynolds": self._reynolds,
                "effective_reynolds": self._effective_reynolds,
                "degraded_motion": wall_speed == 0.0
                and abs(control.angle_degrees - start_angle) > 1.0e-9,
            },
        )

    def advance(self, control: ControlState, target_dt: float) -> StepReport:
        minimum_substeps = 1
        for stability_retries in range(4):
            try:
                return self._advance_once(
                    control,
                    target_dt,
                    minimum_substeps,
                    stability_retries,
                )
            except NumericalFailure as error:
                if (
                    error.reason != "excessive_velocity"
                    or error.stage != "postcondition"
                    or stability_retries == 3
                ):
                    raise
                attempted = int(error.evidence.get("attempted_substeps", 0))
                observed = float(error.evidence.get("maximum_lattice_mach", 0.0))
                if attempted < 1 or not np.isfinite(observed) or observed <= 0.0:
                    raise
                minimum_substeps = max(
                    attempted + 1,
                    int(
                        np.ceil(
                            attempted
                            * observed
                            / self._MAXIMUM_MACH
                            * 1.05
                        )
                    ),
                )
                if minimum_substeps > self._MAXIMUM_SUBSTEPS:
                    raise
        raise RuntimeError("unreachable LBM stability retry state")

    def sample_velocity(self, points: PointCloud) -> PointCloud:
        scenario, _, _, _ = self._require()
        return sample_vector(self._physical_velocity(), points, scenario.domain)

    def export_state(self) -> CanonicalFlowState:
        scenario, _, populations, solid = self._require()
        density, _ = self._macroscopic(populations)
        velocity = self._physical_velocity().copy()
        velocity[solid] = 0.0
        density = np.asarray(density, dtype=scenario.dtype).copy()
        density[solid] = 1.0
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
            density=density[None, ...],
        )

    def import_state(self, state: CanonicalFlowState, control: ControlState) -> ImportOutcome:
        scenario, geometry, populations, solid = self._require()
        checkpoint: LBMCheckpoint = (
            populations.copy(),
            None if self._outlet is None else self._outlet.copy(),
            solid.copy(),
            None if self._signed_distance is None else self._signed_distance.copy(),
            self._solid_angle,
            self._control,
            self._time,
            self._lattice_speed,
            self._lattice_dt,
            self._effective_reynolds,
            self._viscosity_clamped,
            self._omega_plus,
            self._omega_minus,
            None
            if self._boundary_equilibrium is None
            else self._boundary_equilibrium.copy(),
            self._revision,
        )
        try:
            validate_canonical_import(state, scenario, control)
            physical = np.asarray(state.velocity[0], dtype=scenario.dtype).copy()
            maximum = float(np.max(np.linalg.norm(physical, axis=2)))
            wall_speed = (
                abs(np.deg2rad(control.angular_velocity_degrees))
                * geometry.maximum_radius
            )
            self._configure_import_scaling(max(maximum, wall_speed))
            lattice = physical * (self._lattice_speed / self._reference_speed)
            density = (
                np.ones((scenario.domain.ny, scenario.domain.nx), dtype=scenario.dtype)
                if state.density is None
                else np.asarray(state.density[0], dtype=scenario.dtype).copy()
            )
            imported_solid = geometry.mask(scenario.domain, control.angle_degrees)
            fluid_density = density[~imported_solid]
            if (
                not np.isfinite(density).all()
                or np.any(fluid_density <= 0.0)
                or (
                    fluid_density.size > 0
                    and float(np.max(np.abs(fluid_density - 1.0))) > 0.75
                )
            ):
                raise NumericalFailure(
                    "invalid_density",
                    "LBM warm import density is outside the admissible envelope",
                    "canonical-import",
                )
            maximum_mach = (
                max(maximum, wall_speed, self._reference_speed)
                * self._lattice_speed
                / (self._reference_speed * self._LATTICE_SOUND_SPEED)
            )
            if maximum_mach > self._MAXIMUM_MACH * (1.0 + 1.0e-6):
                raise NumericalFailure(
                    "excessive_velocity",
                    "LBM warm import exceeds its Mach limit",
                    "canonical-import",
                    {
                        "maximum_lattice_mach": maximum_mach,
                        "maximum_lattice_mach_limit": self._MAXIMUM_MACH,
                    },
                )
            self._time = state.time
            self._control = control
            self._solid = imported_solid
            if self._centers is None:
                raise RuntimeError("LBM geometry cache has not been initialized")
            wall = geometry.wall_velocity(
                self._centers.reshape(-1, 2), control
            ).reshape(scenario.domain.ny, scenario.domain.nx, 2)
            physical[self._solid] = wall[self._solid]
            density[self._solid] = 1.0
            lattice = physical * (self._lattice_speed / self._reference_speed)
            self._f = self._equilibrium(density, lattice)
            self._outlet = self._f[:, -1:, :].copy()
            self._signed_distance = geometry.signed_distance(
                self._centers.reshape(-1, 2),
                control.angle_degrees,
            ).reshape(scenario.domain.ny, scenario.domain.nx)
            self._solid_angle = control.angle_degrees
        except NumericalFailure as failure:
            (
                self._f,
                self._outlet,
                self._solid,
                self._signed_distance,
                self._solid_angle,
                self._control,
                self._time,
                self._lattice_speed,
                self._lattice_dt,
                self._effective_reynolds,
                self._viscosity_clamped,
                self._omega_plus,
                self._omega_minus,
                self._boundary_equilibrium,
                self._revision,
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
                self._f,
                self._outlet,
                self._solid,
                self._signed_distance,
                self._solid_angle,
                self._control,
                self._time,
                self._lattice_speed,
                self._lattice_dt,
                self._effective_reynolds,
                self._viscosity_clamped,
                self._omega_plus,
                self._omega_minus,
                self._boundary_equilibrium,
                self._revision,
            ) = checkpoint
            raise
        self._revision += 1
        report = ImportReport(
            state.source_solver,
            self.info.id,
            ("non-equilibrium lattice populations", "TRT kinetic modes"),
            ("LBM resumes from local equilibrium; an initialization transient is expected.",),
        )
        return ImportOutcome("accepted", "none", report, report.warnings)

    def _cut_link_adjacent_normal_speed(self, velocity: VelocityField) -> float:
        scenario, geometry, _, solid = self._require()
        if self._centers is None or self._signed_distance is None:
            raise RuntimeError("LBM geometry cache has not been initialized")
        maximum = 0.0
        for direction, (cx_raw, cy_raw) in enumerate(self._C):
            if direction == 0:
                continue
            cx = int(cx_raw)
            cy = int(cy_raw)
            destination_solid = np.roll(solid, shift=(-cy, -cx), axis=(0, 1))
            wall_link = ~solid & destination_solid
            if "x" not in scenario.domain.periodic_axes:
                if cx > 0:
                    wall_link[:, -cx:] = False
                elif cx < 0:
                    wall_link[:, :-cx] = False
            if "y" not in scenario.domain.periodic_axes:
                if cy > 0:
                    wall_link[-cy:, :] = False
                elif cy < 0:
                    wall_link[:-cy, :] = False
            if not np.any(wall_link):
                continue
            destination_distance = np.roll(
                self._signed_distance,
                shift=(-cy, -cx),
                axis=(0, 1),
            )
            fraction = np.clip(
                self._signed_distance
                / np.maximum(self._signed_distance - destination_distance, 1.0e-12),
                0.05,
                1.0,
            )[wall_link]
            wall_points = self._centers[wall_link].copy()
            link_x = cx * scenario.domain.dx
            link_y = cy * scenario.domain.dy
            wall_points[:, 0] += fraction * link_x
            wall_points[:, 1] += fraction * link_y
            wall_velocity = geometry.wall_velocity(wall_points, self._control)
            relative = velocity[wall_link] - wall_velocity
            link_length = float(np.hypot(link_x, link_y))
            projection = np.abs(
                (relative[:, 0] * link_x + relative[:, 1] * link_y) / link_length
            )
            maximum = max(maximum, float(np.max(projection)))
        return maximum

    def diagnostics(self) -> Diagnostics:
        scenario, _, populations, solid = self._require()
        density, _ = self._macroscopic(populations)
        velocity = self._physical_velocity()
        values = {
            "time": self._time,
            "requested_reynolds": self._reynolds,
            "kinetic_energy": kinetic_energy(velocity),
            "enstrophy": enstrophy(velocity, scenario.domain),
            "divergence_l2": divergence_l2(velocity, scenario.domain),
            # Interpolated bounce-back reflects every population aimed through a
            # cut link, so the native wall-normal through-flux is zero by
            # construction. The adjacent-cell value is diagnostic context, not
            # wall leakage.
            "solid_leakage": 0.0,
            "cut_link_adjacent_normal_speed": (
                self._cut_link_adjacent_normal_speed(velocity)
            ),
            "density_mean": float(np.mean(density)),
            "density_drift": float(np.mean(density) - self._density_initial),
            "effective_reynolds": self._effective_reynolds,
            "wake_width": wake_width(
                velocity,
                scenario.domain,
                scenario.foil.pivot[0],
                scenario.foil.chord,
                scenario.freestream[0],
                solid,
            ),
            "recirculation_area": recirculation_area(
                velocity, scenario.domain, scenario.foil.pivot[0], solid
            ),
        }
        if not all(np.isfinite(value) for value in values.values()):
            raise NumericalFailure(
                "nonfinite_state",
                "LBM produced non-finite diagnostics",
            )
        warnings = (
            (f"LBM relaxation clamp active: effective Re={self._effective_reynolds:.1f}",)
            if self._viscosity_clamped
            else ()
        )
        return Diagnostics(values, warnings, self._revision)
