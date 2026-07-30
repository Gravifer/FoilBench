"""Particle-based visualization of airfoil separation and stall.

Designed for Manim Community v0.20.1.

The flow is represented only by moving particles.  No streamlines, traced
paths, or particle trails are drawn.

Optional render command (not run by Codex):
    uv run --with manim manim -pqh particle_airfoil_stall.py ParticleAirfoilStall
"""

from __future__ import annotations

import math
from dataclasses import dataclass

import numpy as np
from manim import *


config.pixel_width = 1920
config.pixel_height = 1080
config.frame_width = 16
config.frame_height = 9
config.frame_rate = 60
config.background_color = BLACK
config.disable_caching = True


AIRFOIL_STROKE = "#AFC4D4"
LABEL_COLOR = WHITE
PARTICLE_PALETTE = ("#1D4ED8", "#2563EB", "#3B82F6", "#60A5FA")
WAKE_PARTICLE_PALETTE = ("#2563EB", "#38BDF8", "#60A5FA", "#93C5FD")


def smootherstep(alpha: float) -> float:
    """Quintic easing with zero velocity at both ends."""
    value = float(np.clip(alpha, 0.0, 1.0))
    return value**3 * (value * (value * 6 - 15) + 10)


def mix(a: float, b: float, alpha: float) -> float:
    return a + (b - a) * alpha


@dataclass(frozen=True)
class FlowState:
    angle_degrees: float
    separation_x: float
    separation_strength: float
    boundary_growth: float
    bubble_length: float
    bubble_height: float
    wake_width: float
    turbulence: float


LOW_STATE = FlowState(
    angle_degrees=-4,
    separation_x=0.95,
    separation_strength=0.02,
    boundary_growth=0.08,
    bubble_length=0.75,
    bubble_height=0.20,
    wake_width=0.16,
    turbulence=0.08,
)

MODERATE_STATE = FlowState(
    angle_degrees=-14,
    separation_x=0.58,
    separation_strength=0.55,
    boundary_growth=0.68,
    bubble_length=2.45,
    bubble_height=0.90,
    wake_width=0.72,
    turbulence=0.52,
)

STALL_STATE = FlowState(
    angle_degrees=-25,
    separation_x=0.10,
    separation_strength=1.00,
    boundary_growth=1.00,
    bubble_length=5.10,
    bubble_height=2.15,
    wake_width=1.75,
    turbulence=1.00,
)


class ParticleAirfoilStall(Scene):
    """Two continuous normal-flight-to-stall-to-recovery cycles."""

    PANEL_DIVIDER_Y = 0.90
    AIRFOIL_CENTER = np.array([-0.55, -2.35, 0.0])
    CHORD = 4.60
    FREE_STREAM_SPEED = 2.00

    X_MIN = -8.20
    X_MAX = 8.20
    Y_MIN = -4.28
    Y_MAX = 0.72

    GRID_COLUMNS = 52
    GRID_ROWS = 12
    BOUNDARY_PARTICLES = 140
    WAKE_PARTICLES = 420

    COLLISION_CUTOFF = 0.065
    COLLISION_STRENGTH = 8.0
    AIRFOIL_REPULSION_CUTOFF = 0.13
    AIRFOIL_REPULSION_STRENGTH = 9.0
    COLLISION_UPDATE_INTERVAL = 2
    VELOCITY_RELAXATION_TIME = 0.16
    MAX_PARTICLE_SPEED = 5.0

    TRAIL_SAMPLE_STRIDE = 4
    TRAIL_LAYERS = 3
    TRAIL_SAMPLE_INTERVAL = 0.055

    def construct(self) -> None:
        self.camera.background_color = BLACK

        phase = ValueTracker(0.0)
        clock = ValueTracker(0.0)
        clock.add_updater(lambda tracker, dt: tracker.increment_value(dt))

        particles = self.make_particles()
        particles.set_z_index(1)
        particles.add_updater(
            lambda group, dt: self.update_particles(
                group,
                dt,
                phase.get_value(),
                clock.get_value(),
            )
        )

        afterimages = self.make_afterimages(particles)
        afterimages.set_z_index(0)
        afterimages.add_updater(
            lambda group, dt: self.update_afterimages(
                group,
                particles,
                clock.get_value(),
                dt,
            )
        )

        airfoil = always_redraw(
            lambda: self.make_airfoil(
                self.state_from_phase(phase.get_value()).angle_degrees
            ).set_z_index(5)
        )

        comparison_panel = self.make_comparison_panel(clock)
        comparison_panel.set_z_index(10)

        # The initial frame is already established at t=0.
        self.add(
            clock,
            phase,
            particles,
            afterimages,
            airfoil,
            comparison_panel,
        )

        # Cycle 1: normal flight, rising angle, sustained stall, recovery.
        self.wait(2)
        self.play(
            phase.animate.set_value(2.0),
            run_time=3,
            rate_func=smootherstep,
        )
        self.wait(5)
        self.play(
            phase.animate.set_value(0.0),
            run_time=3,
            rate_func=smootherstep,
        )

        # Cycle 2 repeats while the remaining comparison captions appear.
        self.wait(2)
        self.play(
            phase.animate.set_value(2.0),
            run_time=3,
            rate_func=smootherstep,
        )
        self.wait(5)
        self.play(
            phase.animate.set_value(0.0),
            run_time=3,
            rate_func=smootherstep,
        )
        self.wait(0.6)

        particles.clear_updaters()
        afterimages.clear_updaters()
        clock.clear_updaters()

    # ------------------------------------------------------------------
    # Stage interpolation
    # ------------------------------------------------------------------

    def state_from_phase(self, phase: float) -> FlowState:
        if phase <= 1:
            return self.interpolate_state(LOW_STATE, MODERATE_STATE, phase)
        return self.interpolate_state(MODERATE_STATE, STALL_STATE, phase - 1)

    @staticmethod
    def interpolate_state(
        start: FlowState,
        end: FlowState,
        alpha: float,
    ) -> FlowState:
        amount = float(np.clip(alpha, 0.0, 1.0))
        return FlowState(
            **{
                field_name: mix(
                    getattr(start, field_name),
                    getattr(end, field_name),
                    amount,
                )
                for field_name in FlowState.__dataclass_fields__
            }
        )

    # ------------------------------------------------------------------
    # Airfoil geometry
    # ------------------------------------------------------------------

    def naca_surface_y(self, normalized_x: float, upper: bool) -> float:
        """Return a NACA 2412 surface ordinate in local airfoil coordinates."""
        x = float(np.clip(normalized_x, 0.0, 1.0))
        thickness = 0.12
        camber = 0.02
        camber_position = 0.40

        half_thickness = (
            5
            * thickness
            * self.CHORD
            * (
                0.2969 * math.sqrt(max(x, 1e-8))
                - 0.1260 * x
                - 0.3516 * x**2
                + 0.2843 * x**3
                - 0.1036 * x**4
            )
        )

        if x < camber_position:
            camber_y = (
                camber
                / camber_position**2
                * (2 * camber_position * x - x**2)
                * self.CHORD
            )
        else:
            camber_y = (
                camber
                / (1 - camber_position) ** 2
                * (
                    1
                    - 2 * camber_position
                    + 2 * camber_position * x
                    - x**2
                )
                * self.CHORD
            )

        return camber_y + (half_thickness if upper else -half_thickness)

    def surface_slope(self, normalized_x: float, upper: bool) -> float:
        epsilon = 1e-3
        left = max(0.0, normalized_x - epsilon)
        right = min(1.0, normalized_x + epsilon)
        if right == left:
            return 0.0
        return (
            self.naca_surface_y(right, upper)
            - self.naca_surface_y(left, upper)
        ) / ((right - left) * self.CHORD)

    @staticmethod
    def rotate_2d(vector: np.ndarray, angle: float) -> np.ndarray:
        cosine = math.cos(angle)
        sine = math.sin(angle)
        return np.array(
            [
                cosine * vector[0] - sine * vector[1],
                sine * vector[0] + cosine * vector[1],
            ]
        )

    def local_to_world(
        self,
        local_xy: np.ndarray,
        angle_radians: float,
    ) -> np.ndarray:
        world_xy = (
            self.AIRFOIL_CENTER[:2]
            + self.rotate_2d(local_xy, angle_radians)
        )
        return np.array([world_xy[0], world_xy[1], 0.0])

    def world_to_local(
        self,
        world_point: np.ndarray,
        angle_radians: float,
    ) -> np.ndarray:
        relative = world_point[:2] - self.AIRFOIL_CENTER[:2]
        return self.rotate_2d(relative, -angle_radians)

    def make_airfoil(self, angle_degrees: float) -> VMobject:
        samples = 120
        angle = angle_degrees * DEGREES

        upper = [
            self.local_to_world(
                np.array(
                    [
                        (x - 0.5) * self.CHORD,
                        self.naca_surface_y(x, upper=True),
                    ]
                ),
                angle,
            )
            for x in np.linspace(0, 1, samples)
        ]
        lower = [
            self.local_to_world(
                np.array(
                    [
                        (x - 0.5) * self.CHORD,
                        self.naca_surface_y(x, upper=False),
                    ]
                ),
                angle,
            )
            for x in np.linspace(1, 0, samples)
        ]

        body = VMobject()
        body.set_points_as_corners(upper + lower + [upper[0]])
        body.set_fill(opacity=0)
        body.set_stroke(AIRFOIL_STROKE, width=5.0)
        return body

    # ------------------------------------------------------------------
    # Particle field
    # ------------------------------------------------------------------

    def make_particles(self) -> VGroup:
        """Create inlet, boundary-layer, and progressively active wake particles."""
        rng = np.random.default_rng(240712)
        particles = VGroup()
        self.particle_data: list[dict[str, object]] = []

        # A jittered full-frame lattice remains evenly distributed upstream.
        xs = np.linspace(self.X_MIN + 0.18, self.X_MAX - 0.18, self.GRID_COLUMNS)
        ys = np.linspace(self.Y_MIN + 0.22, self.Y_MAX - 0.22, self.GRID_ROWS)

        seeds: list[tuple[np.ndarray, str, float]] = []
        for y in ys:
            for x in xs:
                seeds.append(
                    (
                        np.array(
                            [
                                x + rng.uniform(-0.10, 0.10),
                                y + rng.uniform(-0.10, 0.10),
                                0.0,
                            ]
                        ),
                        "main",
                        0.0,
                    )
                )

        # Extra tracers make the thin attached boundary layer visible at low AoA.
        low_angle = LOW_STATE.angle_degrees * DEGREES
        for index in range(self.BOUNDARY_PARTICLES):
            upper = index % 2 == 0
            normalized_x = (index // 2 + 0.5) / (self.BOUNDARY_PARTICLES / 2)
            normalized_x = float(np.clip(normalized_x + rng.uniform(-0.018, 0.018), 0, 1))
            local_x = (normalized_x - 0.5) * self.CHORD
            offset = rng.uniform(0.035, 0.18)
            surface_y = self.naca_surface_y(normalized_x, upper)
            local_y = surface_y + (offset if upper else -offset)
            seeds.append(
                (
                    self.local_to_world(
                        np.array([local_x, local_y]),
                        low_angle,
                    ),
                    "main",
                    0.0,
                )
            )

        # This reservoir is already present at t=0, but only a small fraction
        # is visible in the narrow low-angle wake.  The remaining particles
        # fade in progressively as the separation region grows.
        trailing_x = 0.5 * self.CHORD
        trailing_y = self.naca_surface_y(1.0, upper=True)
        for index in range(self.WAKE_PARTICLES):
            downstream_fraction = ((index % 60) + 0.5) / 60
            row = index // 60
            local_x = (
                trailing_x
                + 0.10
                + 2.65 * downstream_fraction
                + rng.uniform(-0.035, 0.035)
            )
            local_y = (
                trailing_y
                + (row - 2) * 0.052
                + rng.uniform(-0.025, 0.025)
            )
            seeds.append(
                (
                    self.local_to_world(
                        np.array([local_x, local_y]),
                        low_angle,
                    ),
                    "wake",
                    float(rng.uniform(0.0, 1.0)),
                )
            )

        for index, (position, particle_kind, activation_threshold) in enumerate(seeds):
            seed = (index + 1) * 0.61803398875
            radius = 0.018 + 0.008 * (0.5 + 0.5 * math.sin(seed * 11.7))
            base_opacity = 0.58 + 0.34 * (
                0.5 + 0.5 * math.sin(seed * 7.3)
            )
            palette = (
                WAKE_PARTICLE_PALETTE
                if particle_kind == "wake"
                else PARTICLE_PALETTE
            )
            color_index = int(
                abs(math.sin(seed * 4.91)) * len(palette)
            ) % len(palette)
            initial_opacity = (
                0.0 if particle_kind == "wake" else base_opacity
            )

            particle = Dot(
                point=position,
                radius=radius,
                color=palette[color_index],
                fill_opacity=initial_opacity,
                stroke_width=0,
            )
            particles.add(particle)
            self.particle_data.append(
                {
                    "seed": seed,
                    "wraps": 0.0,
                    "kind": particle_kind,
                    "base_opacity": base_opacity,
                    "activation_threshold": activation_threshold,
                    "last_activation": 0.0,
                    "activation": (
                        0.0 if particle_kind == "wake" else 1.0
                    ),
                    "velocity": np.array(
                        [self.FREE_STREAM_SPEED, 0.0, 0.0]
                    ),
                }
            )

        self.collision_frame_counter = 0
        self.cached_repulsive_accelerations = np.zeros(
            (len(particles), 2),
            dtype=float,
        )
        return particles

    def make_afterimages(self, particles: VGroup) -> VGroup:
        """Create sparse ghost-dot layers for brief transition afterimages."""
        self.trail_sample_indices = list(
            range(0, len(particles), self.TRAIL_SAMPLE_STRIDE)
        )
        initial_snapshot = np.array(
            [
                particles[index].get_center().copy()
                for index in self.trail_sample_indices
            ]
        )
        self.trail_history = [
            initial_snapshot.copy()
            for _ in range(self.TRAIL_LAYERS + 1)
        ]
        self.trail_sample_elapsed = 0.0

        layers = VGroup()
        for layer_index in range(self.TRAIL_LAYERS):
            layer = VGroup(
                *[
                    Dot(
                        point=position,
                        radius=0.017 - layer_index * 0.0015,
                        color="#2563EB",
                        fill_opacity=0,
                        stroke_width=0,
                    )
                    for position in initial_snapshot
                ]
            )
            layers.add(layer)
        return layers

    def update_afterimages(
        self,
        afterimages: VGroup,
        particles: VGroup,
        time: float,
        dt: float,
    ) -> None:
        self.trail_sample_elapsed += dt
        while self.trail_sample_elapsed >= self.TRAIL_SAMPLE_INTERVAL:
            snapshot = np.array(
                [
                    particles[index].get_center().copy()
                    for index in self.trail_sample_indices
                ]
            )
            self.trail_history.insert(0, snapshot)
            self.trail_history = self.trail_history[
                : self.TRAIL_LAYERS + 1
            ]
            self.trail_sample_elapsed -= self.TRAIL_SAMPLE_INTERVAL

        intensity = self.afterimage_intensity(time)
        layer_opacities = (0.18, 0.10, 0.055)

        for layer_index, layer in enumerate(afterimages):
            snapshot = self.trail_history[
                min(layer_index + 1, len(self.trail_history) - 1)
            ]
            opacity = intensity * layer_opacities[layer_index]
            for ghost, position, particle_index in zip(
                layer,
                snapshot,
                self.trail_sample_indices,
            ):
                ghost.move_to(position)
                activation = float(
                    self.particle_data[particle_index]["activation"]
                )
                ghost.set_opacity(opacity * activation)

    @staticmethod
    def afterimage_intensity(time: float) -> float:
        """Gate trails around the beginning and end of every angle change."""
        transition_boundaries = (2, 5, 10, 13, 15, 18, 23, 26)
        strongest = 0.0
        for boundary in transition_boundaries:
            start = boundary - 0.45
            end = boundary + 0.55
            fade_in = smootherstep((time - start) / 0.14)
            fade_out = smootherstep((end - time) / 0.18)
            strongest = max(strongest, fade_in * fade_out)
        return strongest

    def update_particles(
        self,
        particles: VGroup,
        dt: float,
        phase: float,
        time: float,
    ) -> None:
        if dt <= 0:
            return

        state = self.state_from_phase(phase)
        visible_wake_fraction = mix(
            0.08,
            1.0,
            smootherstep(float(np.clip(phase / 2, 0, 1))),
        )

        positions = np.array(
            [particle.get_center().copy() for particle in particles],
            dtype=float,
        )
        velocities = np.array(
            [
                np.asarray(particle_data["velocity"], dtype=float).copy()
                for particle_data in self.particle_data
            ]
        )
        activations = np.ones(len(self.particle_data), dtype=float)

        for index, (particle, particle_data) in enumerate(
            zip(particles, self.particle_data)
        ):
            if particle_data["kind"] == "wake":
                activation = smootherstep(
                    (
                        visible_wake_fraction
                        - float(particle_data["activation_threshold"])
                        + 0.08
                    )
                    / 0.16
                )
                activations[index] = activation
                particle_data["activation"] = activation
                particle.set_opacity(
                    float(particle_data["base_opacity"]) * activation
                )

                # When a reserved particle first becomes visible, introduce it
                # inside the current separation/wake volume and fade it in.
                if (
                    activation > 0.02
                    and float(particle_data["last_activation"]) <= 0.02
                ):
                    particle_data["wraps"] = (
                        float(particle_data["wraps"]) + 1
                    )
                    positions[index] = self.spawn_wake_particle(
                        particle_data,
                        state,
                    )
                    velocities[index] = self.velocity_field(
                        positions[index],
                        phase,
                        time,
                        float(particle_data["seed"]),
                    )
                particle_data["last_activation"] = activation
            else:
                particle_data["activation"] = 1.0

        should_recompute_collisions = (
            self.collision_frame_counter
            % self.COLLISION_UPDATE_INTERVAL
            == 0
        )
        if should_recompute_collisions:
            self.cached_repulsive_accelerations = (
                self.compute_particle_repulsion(
                    positions,
                    activations,
                )
            )
        repulsive_accelerations = (
            self.cached_repulsive_accelerations
            * activations[:, np.newaxis]
        )
        self.collision_frame_counter += 1

        # Simultaneous substeps prevent order-dependent motion.  The cached
        # linear collision acceleration is reused throughout this frame.
        substeps = max(1, int(math.ceil(dt / 0.018)))
        step_dt = dt / substeps

        for substep in range(substeps):
            sample_time = time - dt + (substep + 1) * step_dt
            next_positions = positions.copy()
            next_velocities = velocities.copy()

            for index, particle_data in enumerate(self.particle_data):
                target_velocity = self.velocity_field(
                    positions[index],
                    phase,
                    sample_time,
                    float(particle_data["seed"]),
                )
                acceleration = (
                    target_velocity - velocities[index]
                ) / self.VELOCITY_RELAXATION_TIME
                acceleration += np.array(
                    [
                        repulsive_accelerations[index, 0],
                        repulsive_accelerations[index, 1],
                        0.0,
                    ]
                )
                acceleration += self.airfoil_repulsive_acceleration(
                    positions[index],
                    phase,
                )

                velocity = velocities[index] + acceleration * step_dt
                speed = float(np.linalg.norm(velocity[:2]))
                if speed > self.MAX_PARTICLE_SPEED:
                    velocity *= self.MAX_PARTICLE_SPEED / speed

                candidate = positions[index] + velocity * step_dt
                projected = self.keep_outside_airfoil(candidate, phase)
                was_projected = (
                    np.linalg.norm(projected - candidate) > 1e-8
                )
                recycled = self.recycle_particle(
                    projected,
                    particle_data,
                    phase,
                )
                was_recycled = (
                    np.linalg.norm(recycled - projected) > 0.20
                )

                if was_projected or was_recycled:
                    velocity = self.velocity_field(
                        recycled,
                        phase,
                        sample_time,
                        float(particle_data["seed"]),
                    )

                next_positions[index] = recycled
                next_velocities[index] = velocity

            positions = next_positions
            velocities = next_velocities

        for index, particle in enumerate(particles):
            particle.move_to(positions[index])
            self.particle_data[index]["velocity"] = velocities[index]

    def compute_particle_repulsion(
        self,
        positions: np.ndarray,
        activations: np.ndarray,
    ) -> np.ndarray:
        """Return linear cutoff accelerations using a local spatial hash."""
        particle_count = len(positions)
        accelerations = np.zeros((particle_count, 2), dtype=float)
        cell_size = self.COLLISION_CUTOFF
        cutoff_squared = self.COLLISION_CUTOFF**2
        cells: dict[tuple[int, int], list[int]] = {}

        for index, position in enumerate(positions):
            if activations[index] <= 0.02:
                continue
            key = (
                math.floor(position[0] / cell_size),
                math.floor(position[1] / cell_size),
            )
            cells.setdefault(key, []).append(index)

        neighbor_offsets = (
            (0, 0),
            (1, -1),
            (1, 0),
            (1, 1),
            (0, 1),
        )

        for cell_key, cell_indices in cells.items():
            for offset_x, offset_y in neighbor_offsets:
                neighbor_key = (
                    cell_key[0] + offset_x,
                    cell_key[1] + offset_y,
                )
                neighbor_indices = cells.get(neighbor_key)
                if neighbor_indices is None:
                    continue

                for local_index, first in enumerate(cell_indices):
                    start = (
                        local_index + 1
                        if neighbor_key == cell_key
                        else 0
                    )
                    for second in neighbor_indices[start:]:
                        delta = positions[second, :2] - positions[first, :2]
                        distance_squared = float(np.dot(delta, delta))
                        if distance_squared >= cutoff_squared:
                            continue

                        if distance_squared < 1e-12:
                            angle = (
                                first * 2.399963
                                + second * 0.754878
                            ) % TAU
                            direction = np.array(
                                [math.cos(angle), math.sin(angle)]
                            )
                            distance = 0.0
                        else:
                            distance = math.sqrt(distance_squared)
                            direction = delta / distance

                        overlap_fraction = (
                            1 - distance / self.COLLISION_CUTOFF
                        )
                        magnitude = (
                            self.COLLISION_STRENGTH
                            * overlap_fraction
                            * activations[first]
                            * activations[second]
                        )
                        force = direction * magnitude

                        accelerations[first] -= force
                        accelerations[second] += force

        return accelerations

    def airfoil_repulsive_acceleration(
        self,
        world_point: np.ndarray,
        phase: float,
    ) -> np.ndarray:
        """Apply a linear cutoff normal force near the hollow airfoil."""
        state = self.state_from_phase(phase)
        angle = state.angle_degrees * DEGREES
        local = self.world_to_local(world_point, angle)
        normalized_x = local[0] / self.CHORD + 0.5

        if not 0 <= normalized_x <= 1:
            return np.zeros(3)

        upper_y = self.naca_surface_y(normalized_x, upper=True)
        lower_y = self.naca_surface_y(normalized_x, upper=False)
        center_y = 0.5 * (upper_y + lower_y)
        upper_side = local[1] >= center_y
        surface_y = upper_y if upper_side else lower_y
        outward_distance = (
            local[1] - surface_y
            if upper_side
            else surface_y - local[1]
        )

        if outward_distance >= self.AIRFOIL_REPULSION_CUTOFF:
            return np.zeros(3)

        slope = self.surface_slope(normalized_x, upper_side)
        local_normal = (
            np.array([-slope, 1.0])
            if upper_side
            else np.array([slope, -1.0])
        )
        local_normal /= np.linalg.norm(local_normal)

        proximity = 1 - max(outward_distance, 0.0) / (
            self.AIRFOIL_REPULSION_CUTOFF
        )
        magnitude = (
            self.AIRFOIL_REPULSION_STRENGTH
            * float(np.clip(proximity, 0, 1))
        )
        world_normal = self.rotate_2d(local_normal, angle)
        return np.array(
            [
                world_normal[0] * magnitude,
                world_normal[1] * magnitude,
                0.0,
            ]
        )

    def velocity_field(
        self,
        world_point: np.ndarray,
        phase: float,
        time: float,
        particle_seed: float,
    ) -> np.ndarray:
        """A compact illustrative flow model, tuned for educational clarity."""
        state = self.state_from_phase(phase)
        angle = state.angle_degrees * DEGREES
        local = self.world_to_local(world_point, angle)
        local_x, local_y = local
        normalized_x = local_x / self.CHORD + 0.5

        free_stream_world = np.array([self.FREE_STREAM_SPEED, 0.0])
        velocity = self.rotate_2d(free_stream_world, -angle)

        # Smooth obstacle displacement bends the particle field around the wing.
        camber_y = self.naca_surface_y(
            float(np.clip(normalized_x, 0, 1)),
            upper=True,
        )
        lower_y = self.naca_surface_y(
            float(np.clip(normalized_x, 0, 1)),
            upper=False,
        )
        center_y = 0.5 * (camber_y + lower_y)
        side = 1 if local_y >= center_y else -1

        chord_envelope = math.exp(
            -((local_x / (0.66 * self.CHORD)) ** 4)
        )
        vertical_scale = 0.52 + 0.55 * state.separation_strength
        vertical_envelope = math.exp(
            -((abs(local_y - center_y) / vertical_scale) ** 2)
        )
        body_influence = chord_envelope * vertical_envelope

        velocity[1] += (
            side
            * (1.05 + 0.82 * state.separation_strength)
            * body_influence
        )
        velocity[0] *= max(0.38, 1 - 0.31 * body_influence)

        # Attached boundary layer: particles align with the surface and slow.
        if 0 <= normalized_x <= 1:
            upper_side = local_y >= center_y
            surface_y = self.naca_surface_y(normalized_x, upper_side)
            outward_distance = (
                local_y - surface_y
                if upper_side
                else surface_y - local_y
            )

            if 0 <= outward_distance < 0.38:
                proximity = smootherstep(1 - outward_distance / 0.38)
                attached_fraction = 1.0
                if upper_side:
                    separation_ramp = smootherstep(
                        (normalized_x - state.separation_x) / 0.12
                    )
                    attached_fraction -= (
                        state.separation_strength * separation_ramp
                    )

                tangent = np.array(
                    [
                        1.0,
                        self.surface_slope(normalized_x, upper_side),
                    ]
                )
                tangent /= np.linalg.norm(tangent)

                # The close particles are visibly slower in the growing
                # upper-surface boundary layer.
                surface_speed = self.FREE_STREAM_SPEED * mix(
                    0.34,
                    0.82,
                    outward_distance / 0.38,
                )
                if upper_side:
                    surface_speed *= mix(
                        1.0,
                        0.52,
                        state.boundary_growth
                        * smootherstep(normalized_x),
                    )

                target = tangent * surface_speed
                blend = proximity * max(0.0, attached_fraction)
                velocity = (1 - blend) * velocity + blend * target

        # A detached shear band rises from the moving separation point.
        separation_local_x = (state.separation_x - 0.5) * self.CHORD
        separation_surface_y = self.naca_surface_y(
            state.separation_x,
            upper=True,
        )
        bubble_progress = (
            local_x - separation_local_x
        ) / max(state.bubble_length, 1e-3)

        if state.separation_strength > 0.03 and -0.06 <= bubble_progress <= 1.22:
            clamped_progress = float(np.clip(bubble_progress, 0, 1))
            shear_y = (
                separation_surface_y
                + 0.13
                + state.bubble_height
                * 0.78
                * math.sin(math.pi * clamped_progress)
            )
            shear_slope = (
                state.bubble_height
                * 0.78
                * math.pi
                * math.cos(math.pi * clamped_progress)
                / max(state.bubble_length, 1e-3)
            )
            distance_to_shear = abs(local_y - shear_y)
            shear_blend = (
                state.separation_strength
                * math.exp(-((distance_to_shear / 0.22) ** 2))
            )
            shear_tangent = np.array([1.0, shear_slope])
            shear_tangent /= np.linalg.norm(shear_tangent)
            shear_target = shear_tangent * (
                self.FREE_STREAM_SPEED
                * mix(0.52, 0.78, state.separation_strength)
            )
            velocity = (
                (1 - 0.72 * shear_blend) * velocity
                + 0.72 * shear_blend * shear_target
            )

        # Clockwise circulation inside the separated bubble creates leftward
        # particle motion near the upper surface.
        bubble_center = np.array(
            [
                separation_local_x + 0.46 * state.bubble_length,
                separation_surface_y + 0.47 * state.bubble_height + 0.10,
            ]
        )
        bubble_radius_x = max(0.34, 0.56 * state.bubble_length)
        bubble_radius_y = max(0.20, 0.62 * state.bubble_height)
        bubble_delta = local - bubble_center
        bubble_radius_squared = (
            (bubble_delta[0] / bubble_radius_x) ** 2
            + (bubble_delta[1] / bubble_radius_y) ** 2
        )

        if (
            state.separation_strength > 0.08
            and bubble_radius_squared < 1.45
            and local_y > separation_surface_y - 0.08
        ):
            bubble_envelope = (
                state.separation_strength
                * smootherstep(1 - bubble_radius_squared / 1.45)
            )
            clockwise_velocity = np.array(
                [
                    1.55 * bubble_delta[1] / bubble_radius_y,
                    -1.30 * bubble_delta[0] / bubble_radius_x,
                ]
            )
            recirculation_target = (
                clockwise_velocity
                * mix(0.70, 1.42, state.separation_strength)
            )
            velocity = (
                (1 - 0.82 * bubble_envelope) * velocity
                + 0.82 * bubble_envelope * recirculation_target
            )

        # Alternating, advecting vortex cores broaden the turbulent wake.
        trailing_x = 0.5 * self.CHORD
        wake_distance = local_x - trailing_x
        if wake_distance > -0.18:
            wake_axis_y = self.naca_surface_y(1.0, upper=True)
            wake_envelope = math.exp(
                -(
                    (local_y - wake_axis_y)
                    / max(0.16, 1.30 * state.wake_width)
                )
                ** 2
            )
            downstream_envelope = smootherstep(
                (wake_distance + 0.18) / 0.55
            )
            turbulent_envelope = wake_envelope * downstream_envelope

            for vortex_index in range(5):
                spacing = 1.38
                travel = (
                    time
                    * mix(0.58, 0.82, state.turbulence)
                    + vortex_index * spacing
                ) % 7.0
                vortex_x = trailing_x + 0.42 + travel
                alternating = 1 if vortex_index % 2 == 0 else -1
                vortex_y = (
                    wake_axis_y
                    + alternating
                    * state.wake_width
                    * (0.20 + 0.10 * vortex_index)
                    + 0.07
                    * state.wake_width
                    * math.sin(time * 1.1 + vortex_index)
                )
                delta = local - np.array([vortex_x, vortex_y])
                core = mix(0.13, 0.50, state.turbulence)
                radius_squared = float(np.dot(delta, delta))
                strength = (
                    alternating
                    * mix(0.035, 0.88, state.turbulence)
                    * turbulent_envelope
                )
                velocity += (
                    strength
                    * np.array([-delta[1], delta[0]])
                    / (radius_squared + core**2)
                )

            # Small deterministic irregularity keeps the wake unsteady without
            # requiring random changes from frame to frame.
            phase_noise = (
                math.sin(
                    2.15 * local_x
                    - 2.75 * time
                    + particle_seed * 5.1
                )
                + 0.55
                * math.sin(
                    3.7 * local_x
                    + 1.6 * local_y
                    - 4.1 * time
                )
            )
            velocity[1] += (
                0.26
                * state.turbulence
                * turbulent_envelope
                * phase_noise
            )
            velocity[1] += (
                0.14
                * state.turbulence
                * turbulent_envelope
                * math.sin(
                    6.4 * local_x
                    - 5.2 * time
                    + particle_seed * 8.3
                )
            )
            velocity[0] *= (
                1
                - 0.42
                * state.turbulence
                * turbulent_envelope
            )

        world_velocity = self.rotate_2d(velocity, angle)
        return np.array([world_velocity[0], world_velocity[1], 0.0])

    def keep_outside_airfoil(
        self,
        world_point: np.ndarray,
        phase: float,
    ) -> np.ndarray:
        """Project any particle that crossed the solid wing back outside."""
        state = self.state_from_phase(phase)
        angle = state.angle_degrees * DEGREES
        local = self.world_to_local(world_point, angle)
        normalized_x = local[0] / self.CHORD + 0.5

        if not 0 <= normalized_x <= 1:
            return world_point

        upper_y = self.naca_surface_y(normalized_x, upper=True)
        lower_y = self.naca_surface_y(normalized_x, upper=False)
        margin = 0.030

        if lower_y - margin < local[1] < upper_y + margin:
            center_y = 0.5 * (upper_y + lower_y)
            local[1] = (
                upper_y + margin
                if local[1] >= center_y
                else lower_y - margin
            )
            return self.local_to_world(local, angle)

        return world_point

    @staticmethod
    def pseudo_random_fraction(value: float) -> float:
        return 0.5 + 0.5 * math.sin(value * 12.9898 + 78.233)

    def spawn_wake_particle(
        self,
        particle_data: dict[str, object],
        state: FlowState,
    ) -> np.ndarray:
        """Place a reserved tracer inside the current separated-flow volume."""
        seed = float(particle_data["seed"])
        wraps = float(particle_data["wraps"])
        fraction_x = self.pseudo_random_fraction(seed + wraps * 0.73)
        fraction_y = self.pseudo_random_fraction(seed * 1.91 + wraps * 1.17)
        jitter = self.pseudo_random_fraction(seed * 3.17 + wraps * 0.41) - 0.5

        separation_x = (state.separation_x - 0.5) * self.CHORD
        trailing_x = 0.5 * self.CHORD
        spawn_start = mix(
            trailing_x + 0.08,
            separation_x + 0.10,
            state.separation_strength,
        )
        spawn_length = mix(
            1.55,
            state.bubble_length + 3.15,
            state.separation_strength,
        )
        local_x = spawn_start + fraction_x * spawn_length

        if local_x <= trailing_x:
            normalized_x = float(
                np.clip(local_x / self.CHORD + 0.5, 0, 1)
            )
            surface_y = self.naca_surface_y(normalized_x, upper=True)
            bubble_progress = float(
                np.clip(
                    (local_x - separation_x)
                    / max(state.bubble_length, 1e-3),
                    0,
                    1,
                )
            )
            available_height = (
                0.10
                + state.bubble_height
                * (
                    0.22
                    + 0.78 * math.sin(math.pi * bubble_progress)
                )
            )
            local_y = (
                surface_y
                + 0.055
                + fraction_y * available_height
                + jitter * 0.06
            )
        else:
            wake_axis_y = self.naca_surface_y(1.0, upper=True)
            downstream_growth = smootherstep(
                (local_x - trailing_x) / 1.70
            )
            local_wake_width = max(
                0.12,
                state.wake_width * mix(0.38, 1.0, downstream_growth),
            )
            local_y = (
                wake_axis_y
                + (2 * fraction_y - 1) * local_wake_width
                + 0.16 * state.separation_strength
                + jitter * 0.08
            )

        angle = state.angle_degrees * DEGREES
        return self.local_to_world(
            np.array([local_x, local_y]),
            angle,
        )

    def recycle_particle(
        self,
        position: np.ndarray,
        particle_data: dict[str, object],
        phase: float,
    ) -> np.ndarray:
        """Maintain steady inlet density and a persistent turbulent wake."""
        if particle_data["kind"] == "wake":
            state = self.state_from_phase(phase)
            angle = state.angle_degrees * DEGREES
            local = self.world_to_local(position, angle)
            separation_x = (state.separation_x - 0.5) * self.CHORD
            trailing_x = 0.5 * self.CHORD

            outside_wake_volume = (
                position[0] > self.X_MAX
                or position[1] > self.Y_MAX + 0.18
                or position[1] < self.Y_MIN - 0.18
                or local[0] < separation_x - 0.38
                or local[0] > trailing_x + 7.15
            )
            if outside_wake_volume:
                particle_data["wraps"] = (
                    float(particle_data["wraps"]) + 1
                )
                return self.spawn_wake_particle(particle_data, state)
            return position

        outside_right = position[0] > self.X_MAX
        outside_vertical = (
            position[1] > self.Y_MAX + 0.18
            or position[1] < self.Y_MIN - 0.18
        )

        if outside_right or outside_vertical:
            particle_data["wraps"] = float(particle_data["wraps"]) + 1
            seed = (
                float(particle_data["seed"]) * 12.9898
                + float(particle_data["wraps"]) * 78.233
            )
            fraction = 0.5 + 0.5 * math.sin(seed)
            position = np.array(
                [
                    self.X_MIN,
                    mix(self.Y_MIN + 0.12, self.Y_MAX - 0.12, fraction),
                    0.0,
                ]
            )

        if position[0] < self.X_MIN - 0.25:
            position[0] = self.X_MIN
        return position

    # ------------------------------------------------------------------
    # Persistent comparison captions in the upper 40 percent
    # ------------------------------------------------------------------

    def make_comparison_panel(self, clock: ValueTracker) -> VGroup:
        horizontal_divider = Line(
            np.array([-8.0, self.PANEL_DIVIDER_Y, 0.0]),
            np.array([8.0, self.PANEL_DIVIDER_Y, 0.0]),
            color="#334155",
            stroke_width=2.0,
        )
        vertical_divider = Line(
            np.array([0.0, 1.24, 0.0]),
            np.array([0.0, 4.18, 0.0]),
            color="#334155",
            stroke_width=1.5,
        )

        laminar_heading = self.make_latex_caption(
            ("laminar flow,", "plane flies normally"),
            font_size=32,
            color=LABEL_COLOR,
            bold=True,
        ).move_to(np.array([-4.0, 3.72, 0.0]))

        laminar_layers = self.make_latex_caption(
            ("Fluid flows in layers",),
            font_size=30,
            color="#93C5FD",
        ).move_to(np.array([-4.0, 2.63, 0.0]))

        laminar_no_mixing = self.make_latex_caption(
            ("No mixing between layers",),
            font_size=30,
            color="#93C5FD",
        ).move_to(np.array([-4.0, 1.74, 0.0]))

        turbulent_heading = self.make_latex_caption(
            ("turbulent flow,", "plane stalls."),
            font_size=32,
            color=LABEL_COLOR,
            bold=True,
        ).move_to(np.array([4.0, 3.72, 0.0]))

        turbulent_feature = self.make_latex_caption(
            ("Chaotic, unpredictable,", "and mixing"),
            font_size=30,
            color="#38BDF8",
        ).move_to(np.array([4.0, 2.20, 0.0]))

        # Cycle 1 reveals the first laminar feature and turbulent heading.
        self.attach_reveal_updater(laminar_heading, clock, reveal_time=0.35)
        self.attach_reveal_updater(laminar_layers, clock, reveal_time=1.00)
        self.attach_reveal_updater(turbulent_heading, clock, reveal_time=5.25)

        # Cycle 2 adds the remaining comparison without removing earlier text.
        self.attach_reveal_updater(
            laminar_no_mixing,
            clock,
            reveal_time=13.45,
        )
        self.attach_reveal_updater(
            turbulent_feature,
            clock,
            reveal_time=18.25,
        )

        return VGroup(
            horizontal_divider,
            vertical_divider,
            laminar_heading,
            laminar_layers,
            laminar_no_mixing,
            turbulent_heading,
            turbulent_feature,
        )

    @staticmethod
    def make_latex_caption(
        lines: tuple[str, ...],
        font_size: float,
        color: ManimColor,
        bold: bool = False,
    ) -> VGroup:
        """Create prose captions using LaTeX's Computer Modern font."""
        tex_lines = VGroup()
        for line in lines:
            source = rf"\textbf{{{line}}}" if bold else line
            tex_lines.add(
                Tex(
                    source,
                    font_size=font_size,
                    color=color,
                )
            )
        tex_lines.arrange(DOWN, buff=0.08)
        return tex_lines

    @staticmethod
    def attach_reveal_updater(
        caption: Mobject,
        clock: ValueTracker,
        reveal_time: float,
    ) -> None:
        final_position = caption.get_center().copy()
        fade_duration = 0.42

        def reveal(mobject: Mobject) -> None:
            alpha = smootherstep(
                (clock.get_value() - reveal_time) / fade_duration
            )
            mobject.set_opacity(alpha)
            mobject.move_to(
                final_position + DOWN * 0.10 * (1 - alpha)
            )

        caption.add_updater(reveal)
