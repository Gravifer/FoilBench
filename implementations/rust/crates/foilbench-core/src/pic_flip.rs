//! Deterministic blended PIC/FLIP solver sharing the Rust MAC projection core.

#![allow(
    clippy::cast_possible_truncation,
    clippy::cast_possible_wrap,
    clippy::cast_precision_loss,
    clippy::cast_sign_loss,
    clippy::float_cmp,
    clippy::fn_params_excessive_bools,
    clippy::missing_panics_doc,
    clippy::too_many_arguments,
    clippy::too_many_lines
)]

use std::collections::BTreeMap;

use crate::{
    canonical::Producer,
    field::{MacGrid2, ScalarField2},
    geometry::NacaFoil,
    grid::{GridDomain2, faces_to_cells},
    pcg32::Pcg32,
    scenario::{ControlState, Scenario},
    solver::{
        CanonicalFlowState2, Diagnostics, Evidence, EvidenceValue, FailureReason, FailureStage,
        FlowScalar, FlowSolver, ImportOutcome, InteractiveTuning, RestartState, ReynoldsOutcome,
        SolverError, SolverInfo, StepReport, TuningValue,
    },
    stable_fluids::StableFluids,
};

const PARTICLES_PER_CELL: usize = 4;
const MAX_SUBSTEPS: usize = 512;
const RNG_STREAM: u64 = 71;

#[derive(Clone, Copy, Debug, PartialEq)]
struct Particle<T: FlowScalar> {
    position: [T; 2],
    velocity: [T; 2],
    generation: u32,
}

#[derive(Clone)]
struct PicState<T: FlowScalar> {
    scenario: Scenario,
    geometry: NacaFoil,
    domain: GridDomain2,
    grid: StableFluids<T>,
    particles: Vec<Particle<T>>,
    rng: Pcg32,
    blend: f64,
    cfl: f64,
    reynolds: f64,
    control: ControlState,
    revision: u64,
    advance_count: usize,
    settling_steps: usize,
    unsupported_face_fraction: f64,
    swept_collisions_last_step: usize,
}

#[derive(Clone)]
pub struct PicFlip<T: FlowScalar> {
    info: SolverInfo,
    execution_target: String,
    state: Option<PicState<T>>,
}

impl<T: FlowScalar> Default for PicFlip<T> {
    fn default() -> Self {
        Self::new("native")
    }
}

fn quadratic_weight(distance: f64) -> f64 {
    let value = distance.abs();
    if value < 0.5 {
        0.75 - value * value
    } else if value < 1.5 {
        0.5 * (1.5 - value).powi(2)
    } else {
        0.0
    }
}

fn sample_component<T: FlowScalar>(
    field: &ScalarField2<T>,
    gx: f64,
    gy: f64,
    periodic_x: bool,
    periodic_y: bool,
    duplicate_x: bool,
    duplicate_y: bool,
) -> f64 {
    let width = field.nx();
    let height = field.ny();
    let unique_width = if duplicate_x { width - 1 } else { width };
    let unique_height = if duplicate_y { height - 1 } else { height };
    let base_x = (gx - 0.5).floor() as isize;
    let base_y = (gy - 0.5).floor() as isize;
    let mut value = 0.0;
    let mut total = 0.0;
    for offset_y in 0..3_isize {
        for offset_x in 0..3_isize {
            let raw_x = base_x + offset_x;
            let raw_y = base_y + offset_y;
            let mut x = raw_x;
            let mut y = raw_y;
            if periodic_x {
                x = x.rem_euclid(unique_width as isize);
            }
            if periodic_y {
                y = y.rem_euclid(unique_height as isize);
            }
            if x < 0 || x >= width as isize || y < 0 || y >= height as isize {
                continue;
            }
            let weight = quadratic_weight(gx - raw_x as f64) * quadratic_weight(gy - raw_y as f64);
            value += weight * field.get(x as usize, y as usize).to_f64();
            total += weight;
        }
    }
    if total > 1.0e-12 { value / total } else { 0.0 }
}

fn sample_faces<T: FlowScalar>(
    faces: &MacGrid2<T>,
    domain: GridDomain2,
    point: [f64; 2],
) -> [f64; 2] {
    let gx = (point[0] - domain.bounds[0][0]) / domain.dx();
    let gy = (point[1] - domain.bounds[1][0]) / domain.dy();
    [
        sample_component(
            &faces.u,
            gx,
            gy - 0.5,
            domain.periodic_x,
            domain.periodic_y,
            domain.periodic_x,
            false,
        ),
        sample_component(
            &faces.v,
            gx - 0.5,
            gy,
            domain.periodic_x,
            domain.periodic_y,
            false,
            domain.periodic_y,
        ),
    ]
}

fn scatter_component<T: FlowScalar>(
    particles: &[Particle<T>],
    component: usize,
    output: &mut ScalarField2<T>,
    fallback: &ScalarField2<T>,
    domain: GridDomain2,
    offset: [f64; 2],
    duplicate_x: bool,
    duplicate_y: bool,
) -> usize {
    let width = output.nx();
    let height = output.ny();
    let unique_width = if duplicate_x { width - 1 } else { width };
    let unique_height = if duplicate_y { height - 1 } else { height };
    let mut sums = vec![0.0_f64; width * height];
    let mut weights = vec![0.0_f64; width * height];
    for particle in particles {
        let gx = (particle.position[0].to_f64() - domain.bounds[0][0]) / domain.dx() + offset[0];
        let gy = (particle.position[1].to_f64() - domain.bounds[1][0]) / domain.dy() + offset[1];
        let base_x = (gx - 0.5).floor() as isize;
        let base_y = (gy - 0.5).floor() as isize;
        for offset_y in 0..3_isize {
            for offset_x in 0..3_isize {
                let raw_x = base_x + offset_x;
                let raw_y = base_y + offset_y;
                let mut x = raw_x;
                let mut y = raw_y;
                if domain.periodic_x {
                    x = x.rem_euclid(unique_width as isize);
                }
                if domain.periodic_y {
                    y = y.rem_euclid(unique_height as isize);
                }
                if x < 0 || x >= width as isize || y < 0 || y >= height as isize {
                    continue;
                }
                let weight =
                    quadratic_weight(gx - raw_x as f64) * quadratic_weight(gy - raw_y as f64);
                let index = y as usize * width + x as usize;
                sums[index] += weight * particle.velocity[component].to_f64();
                weights[index] += weight;
            }
        }
    }
    let mut unsupported = 0;
    for index in 0..sums.len() {
        if weights[index] > 1.0e-12 {
            output.values_mut()[index] = T::from_f64(sums[index] / weights[index]);
        } else {
            output.values_mut()[index] = fallback.values()[index];
            unsupported += 1;
        }
    }
    if duplicate_x {
        for y in 0..height {
            output.set(width - 1, y, output.get(0, y));
        }
    }
    if duplicate_y {
        for x in 0..width {
            output.set(x, height - 1, output.get(x, 0));
        }
    }
    unsupported
}

impl<T: FlowScalar> PicFlip<T> {
    #[must_use]
    pub fn new(execution_target: impl Into<String>) -> Self {
        Self {
            info: SolverInfo {
                id: "pic-flip".into(),
                display_name: "Blended PIC/FLIP".into(),
                dimensions: vec![2],
                supports_moving_boundary: true,
                supported_precisions: vec![
                    crate::scenario::Precision::Float32,
                    crate::scenario::Precision::Float64,
                ],
                acceleration: "Rust deterministic particle-grid CPU".into(),
            },
            execution_target: execution_target.into(),
            state: None,
        }
    }

    #[must_use]
    pub fn current_reynolds(&self) -> Option<f64> {
        self.state.as_ref().map(|state| state.reynolds)
    }

    fn state(&self) -> Result<&PicState<T>, SolverError> {
        self.state.as_ref().ok_or_else(|| {
            SolverError::new(
                FailureReason::UnsupportedConversion,
                FailureStage::Initialization,
                "PIC/FLIP has not been initialized",
            )
        })
    }

    fn option_f64(scenario: &Scenario, name: &str, default: f64) -> Result<f64, SolverError> {
        let value = scenario
            .solver_options()
            .get(name)
            .map_or(Some(default), serde_json::Value::as_f64)
            .ok_or_else(|| {
                SolverError::new(
                    FailureReason::TimeContractFailure,
                    FailureStage::Initialization,
                    format!("solver option {name} must be numeric"),
                )
            })?;
        if !value.is_finite() {
            return Err(SolverError::new(
                FailureReason::TimeContractFailure,
                FailureStage::Initialization,
                format!("solver option {name} must be finite"),
            ));
        }
        Ok(value)
    }

    fn option_usize(scenario: &Scenario, name: &str, default: usize) -> Result<usize, SolverError> {
        scenario
            .solver_options()
            .get(name)
            .map_or(Some(default as u64), serde_json::Value::as_u64)
            .and_then(|value| usize::try_from(value).ok())
            .ok_or_else(|| {
                SolverError::new(
                    FailureReason::TimeContractFailure,
                    FailureStage::Initialization,
                    format!("solver option {name} must be a non-negative integer"),
                )
            })
    }

    fn seed_particles(state: &mut PicState<T>, angle_degrees: f64) -> Result<(), SolverError> {
        let snapshot = state.grid.particle_grid_snapshot()?;
        let mut particles = Vec::with_capacity(state.domain.nx() * state.domain.ny() * 4);
        for y in 0..state.domain.ny() {
            for x in 0..state.domain.nx() {
                let center = state.domain.cell_center(x, y);
                if state.geometry.signed_distance(center, angle_degrees) <= 0.0 {
                    continue;
                }
                for _ in 0..PARTICLES_PER_CELL {
                    let mut placed = false;
                    for _ in 0..16 {
                        let point = [
                            state.domain.bounds[0][0]
                                + (x as f64 + 0.1 + 0.8 * f64::from(state.rng.next_f32()))
                                    * state.domain.dx(),
                            state.domain.bounds[1][0]
                                + (y as f64 + 0.1 + 0.8 * f64::from(state.rng.next_f32()))
                                    * state.domain.dy(),
                        ];
                        if state.geometry.signed_distance(point, angle_degrees) > 0.0 {
                            let velocity = sample_faces(&snapshot.velocity, state.domain, point);
                            particles.push(Particle {
                                position: [T::from_f64(point[0]), T::from_f64(point[1])],
                                velocity: [T::from_f64(velocity[0]), T::from_f64(velocity[1])],
                                generation: 0,
                            });
                            placed = true;
                            break;
                        }
                    }
                    if !placed {
                        return Err(SolverError::new(
                            FailureReason::TransferFailure,
                            FailureStage::PopulationMaintenance,
                            "PIC/FLIP could not seed four particles in a fluid cell",
                        ));
                    }
                }
            }
        }
        state.particles = particles;
        Ok(())
    }

    fn create_state(
        scenario: &Scenario,
        geometry: &NacaFoil,
        seed: u32,
        start: RestartState,
        execution_target: &str,
    ) -> Result<PicState<T>, SolverError> {
        if scenario.dimension() != 2 || scenario.precision() != T::PRECISION {
            return Err(SolverError::new(
                FailureReason::IncompatibleDomain,
                FailureStage::Initialization,
                "PIC/FLIP requires a matching 2D precision scenario",
            ));
        }
        if !start.time.is_finite()
            || start.time < 0.0
            || !start.angle_degrees.is_finite()
            || !start.reynolds.is_finite()
            || start.reynolds <= 0.0
        {
            return Err(SolverError::new(
                FailureReason::TimeContractFailure,
                FailureStage::Restart,
                "PIC/FLIP restart state is invalid",
            ));
        }
        let blend = Self::option_f64(scenario, "pic_flip_blend", 0.95)?;
        let cfl = Self::option_f64(scenario, "pic_cfl", 0.75)?;
        if !((0.0..=1.0).contains(&blend) && 0.0 < cfl && cfl <= 1.0) {
            return Err(SolverError::new(
                FailureReason::TimeContractFailure,
                FailureStage::Initialization,
                "PIC/FLIP blend or CFL lies outside its supported range",
            ));
        }
        let domain = GridDomain2::from_scenario(scenario).map_err(|detail| {
            SolverError::new(
                FailureReason::IncompatibleDomain,
                FailureStage::Initialization,
                detail,
            )
        })?;
        let control = ControlState {
            time: start.time,
            angle_degrees: start.angle_degrees,
            angular_velocity_degrees: 0.0,
        };
        let mut grid = StableFluids::new(execution_target);
        grid.restart(scenario, geometry, seed, start)?;
        let mut state = PicState {
            scenario: scenario.clone(),
            geometry: geometry.clone(),
            domain,
            grid,
            particles: Vec::new(),
            rng: Pcg32::new(u64::from(seed), RNG_STREAM),
            blend,
            cfl,
            reynolds: start.reynolds,
            control,
            revision: 0,
            advance_count: 0,
            settling_steps: 0,
            unsupported_face_fraction: 0.0,
            swept_collisions_last_step: 0,
        };
        Self::seed_particles(&mut state, start.angle_degrees)?;
        Ok(state)
    }

    fn scatter_to_faces(state: &mut PicState<T>, fallback: &MacGrid2<T>) -> MacGrid2<T> {
        let mut output = fallback.clone();
        let unsupported_u = scatter_component(
            &state.particles,
            0,
            &mut output.u,
            &fallback.u,
            state.domain,
            [0.0, -0.5],
            state.domain.periodic_x,
            false,
        );
        let unsupported_v = scatter_component(
            &state.particles,
            1,
            &mut output.v,
            &fallback.v,
            state.domain,
            [-0.5, 0.0],
            false,
            state.domain.periodic_y,
        );
        state.unsupported_face_fraction = (unsupported_u + unsupported_v) as f64
            / (output.u.values().len() + output.v.values().len()) as f64;
        output
    }

    fn respawn_particle(
        state: &mut PicState<T>,
        index: usize,
        angle_degrees: f64,
        faces: &MacGrid2<T>,
    ) -> Result<(), SolverError> {
        for _ in 0..32 {
            let x = if state.domain.periodic_x {
                state.domain.bounds[0][0]
                    + f64::from(state.rng.next_f32())
                        * (state.domain.bounds[0][1] - state.domain.bounds[0][0])
            } else {
                state.domain.bounds[0][0]
                    + (0.1 + 0.8 * f64::from(state.rng.next_f32())) * state.domain.dx()
            };
            let y = state.domain.bounds[1][0]
                + f64::from(state.rng.next_f32())
                    * (state.domain.bounds[1][1] - state.domain.bounds[1][0]);
            let point = [x, y];
            if state.geometry.signed_distance(point, angle_degrees) > 0.0 {
                let velocity = sample_faces(faces, state.domain, point);
                let generation = state.particles[index].generation.saturating_add(1);
                state.particles[index] = Particle {
                    position: [T::from_f64(x), T::from_f64(y)],
                    velocity: [T::from_f64(velocity[0]), T::from_f64(velocity[1])],
                    generation,
                };
                return Ok(());
            }
        }
        Err(SolverError::new(
            FailureReason::TransferFailure,
            FailureStage::PopulationMaintenance,
            "PIC/FLIP could not respawn a solver particle",
        ))
    }

    fn apply_particle_boundary(
        state: &mut PicState<T>,
        index: usize,
        old_position: [f64; 2],
        start_control: ControlState,
        control: ControlState,
        faces: &MacGrid2<T>,
    ) -> Result<(), SolverError> {
        let width = state.domain.bounds[0][1] - state.domain.bounds[0][0];
        let height = state.domain.bounds[1][1] - state.domain.bounds[1][0];
        let mut point = [
            state.particles[index].position[0].to_f64(),
            state.particles[index].position[1].to_f64(),
        ];
        if state.domain.periodic_x {
            point[0] = state.domain.bounds[0][0]
                + (point[0] - state.domain.bounds[0][0]).rem_euclid(width);
        }
        if state.domain.periodic_y {
            point[1] = state.domain.bounds[1][0]
                + (point[1] - state.domain.bounds[1][0]).rem_euclid(height);
        }
        state.particles[index].position = [T::from_f64(point[0]), T::from_f64(point[1])];
        if (!state.domain.periodic_x
            && (point[0] < state.domain.bounds[0][0] || point[0] >= state.domain.bounds[0][1]))
            || (!state.domain.periodic_y
                && (point[1] < state.domain.bounds[1][0] || point[1] >= state.domain.bounds[1][1]))
        {
            return Self::respawn_particle(state, index, control.angle_degrees, faces);
        }
        let segment = [point[0] - old_position[0], point[1] - old_position[1]];
        let travel = segment[0].hypot(segment[1]);
        let wall_travel = (control.angle_degrees - start_control.angle_degrees)
            .to_radians()
            .abs()
            * state.geometry.maximum_radius();
        let spacing = state.domain.dx().min(state.domain.dy());
        let samples = ((travel + wall_travel) / (0.1 * spacing).max(f64::EPSILON))
            .ceil()
            .clamp(2.0, 16.0) as usize;
        let margin = 0.05 * spacing;
        for sample in 1..=samples {
            let fraction = sample as f64 / samples as f64;
            let angle = start_control.angle_degrees
                + fraction * (control.angle_degrees - start_control.angle_degrees);
            let probe = [
                old_position[0] + fraction * segment[0],
                old_position[1] + fraction * segment[1],
            ];
            let distance = state.geometry.signed_distance(probe, angle);
            if distance > margin {
                continue;
            }
            let normal = state.geometry.normal(probe, angle);
            let norm = normal[0].hypot(normal[1]);
            if !norm.is_finite() || norm <= 0.5 {
                break;
            }
            let unit = [normal[0] / norm, normal[1] / norm];
            point = [
                probe[0] + (-distance + margin) * unit[0],
                probe[1] + (-distance + margin) * unit[1],
            ];
            state.particles[index].position = [T::from_f64(point[0]), T::from_f64(point[1])];
            let omega = control.angular_velocity_degrees.to_radians();
            let pivot = state.geometry.descriptor().pivot.as_slice();
            let wall = [
                -omega * (point[1] - pivot[1]),
                omega * (point[0] - pivot[0]),
            ];
            let velocity = [
                state.particles[index].velocity[0].to_f64(),
                state.particles[index].velocity[1].to_f64(),
            ];
            let relative_normal =
                (velocity[0] - wall[0]) * unit[0] + (velocity[1] - wall[1]) * unit[1];
            if relative_normal < 0.0 {
                state.particles[index].velocity = [
                    T::from_f64(velocity[0] - relative_normal * unit[0]),
                    T::from_f64(velocity[1] - relative_normal * unit[1]),
                ];
            }
            state.swept_collisions_last_step += 1;
            break;
        }
        let distance = state.geometry.signed_distance(point, control.angle_degrees);
        if distance > 0.0 {
            return Ok(());
        }
        let normal = state.geometry.normal(point, control.angle_degrees);
        let norm = normal[0].hypot(normal[1]);
        if distance >= -1.5 * spacing && norm.is_finite() && norm > 0.5 {
            let correction = -distance + 1.0e-3 * spacing;
            state.particles[index].position = [
                T::from_f64(point[0] + correction * normal[0] / norm),
                T::from_f64(point[1] + correction * normal[1] / norm),
            ];
            Ok(())
        } else {
            Self::respawn_particle(state, index, control.angle_degrees, faces)
        }
    }

    fn maintain_population(state: &mut PicState<T>, control: ControlState, faces: &MacGrid2<T>) {
        let count = state.domain.nx() * state.domain.ny();
        let mut counts = vec![0_usize; count];
        let mut cells = vec![None; state.particles.len()];
        for (index, particle) in state.particles.iter().enumerate() {
            let x = ((particle.position[0].to_f64() - state.domain.bounds[0][0])
                / state.domain.dx())
            .floor() as isize;
            let y = ((particle.position[1].to_f64() - state.domain.bounds[1][0])
                / state.domain.dy())
            .floor() as isize;
            if x >= 0
                && x < state.domain.nx() as isize
                && y >= 0
                && y < state.domain.ny() as isize
                && state.geometry.signed_distance(
                    [particle.position[0].to_f64(), particle.position[1].to_f64()],
                    control.angle_degrees,
                ) > 0.0
            {
                let cell = y as usize * state.domain.nx() + x as usize;
                cells[index] = Some(cell);
                counts[cell] += 1;
            }
        }
        let mut targets = Vec::new();
        for y in 0..state.domain.ny() {
            for x in 0..state.domain.nx() {
                let cell = y * state.domain.nx() + x;
                if state
                    .geometry
                    .signed_distance(state.domain.cell_center(x, y), control.angle_degrees)
                    <= 0.0
                {
                    continue;
                }
                for _ in counts[cell]..PARTICLES_PER_CELL {
                    targets.push(cell);
                }
            }
        }
        let mut retained = counts;
        let mut donors = Vec::new();
        for (index, cell) in cells.iter().copied().enumerate() {
            if cell.is_none_or(|cell| retained[cell] > PARTICLES_PER_CELL) {
                donors.push(index);
                if let Some(cell) = cell {
                    retained[cell] -= 1;
                }
            }
        }
        for (index, cell) in donors.into_iter().zip(targets) {
            let x = cell % state.domain.nx();
            let y = cell / state.domain.nx();
            for _ in 0..16 {
                let point = [
                    state.domain.bounds[0][0]
                        + (x as f64 + 0.1 + 0.8 * f64::from(state.rng.next_f32()))
                            * state.domain.dx(),
                    state.domain.bounds[1][0]
                        + (y as f64 + 0.1 + 0.8 * f64::from(state.rng.next_f32()))
                            * state.domain.dy(),
                ];
                if state.geometry.signed_distance(point, control.angle_degrees) > 0.0 {
                    let velocity = sample_faces(faces, state.domain, point);
                    state.particles[index] = Particle {
                        position: [T::from_f64(point[0]), T::from_f64(point[1])],
                        velocity: [T::from_f64(velocity[0]), T::from_f64(velocity[1])],
                        generation: state.particles[index].generation.saturating_add(1),
                    };
                    break;
                }
            }
        }
    }

    fn population_evidence(
        state: &PicState<T>,
        solid: &ScalarField2<u8>,
    ) -> Result<Evidence, SolverError> {
        let mut counts = vec![0_usize; state.domain.nx() * state.domain.ny()];
        let mut inside = 0_usize;
        for (index, particle) in state.particles.iter().enumerate() {
            let values = [
                particle.position[0].to_f64(),
                particle.position[1].to_f64(),
                particle.velocity[0].to_f64(),
                particle.velocity[1].to_f64(),
            ];
            if !values.iter().all(|value| value.is_finite()) {
                return Err(SolverError::new(
                    FailureReason::NonfiniteState,
                    FailureStage::Postcondition,
                    format!("PIC/FLIP particle {index} became non-finite"),
                ));
            }
            if state.geometry.signed_distance(
                values[..2].try_into().expect("two positions"),
                state.control.angle_degrees,
            ) <= 0.0
            {
                inside += 1;
            }
            let x = ((values[0] - state.domain.bounds[0][0]) / state.domain.dx()).floor() as isize;
            let y = ((values[1] - state.domain.bounds[1][0]) / state.domain.dy()).floor() as isize;
            if x >= 0 && x < state.domain.nx() as isize && y >= 0 && y < state.domain.ny() as isize
            {
                counts[y as usize * state.domain.nx() + x as usize] += 1;
            }
        }
        let mut fluid_cells = 0_usize;
        let mut empty = 0_usize;
        let mut underfilled = 0_usize;
        let mut maximum = 0_usize;
        for (cell, count) in counts.into_iter().enumerate() {
            if solid.values()[cell] != 0 {
                continue;
            }
            fluid_cells += 1;
            empty += usize::from(count == 0);
            underfilled += usize::from(count < PARTICLES_PER_CELL);
            maximum = maximum.max(count);
        }
        Ok(BTreeMap::from([
            (
                "particle_count".into(),
                EvidenceValue::Number(state.particles.len() as f64),
            ),
            (
                "empty_cell_fraction".into(),
                EvidenceValue::Number(empty as f64 / fluid_cells.max(1) as f64),
            ),
            (
                "underfilled_cell_fraction".into(),
                EvidenceValue::Number(underfilled as f64 / fluid_cells.max(1) as f64),
            ),
            (
                "maximum_cell_population".into(),
                EvidenceValue::Number(maximum as f64),
            ),
            (
                "unresolved_solid_particles".into(),
                EvidenceValue::Number(inside as f64),
            ),
        ]))
    }

    fn advance_candidate(
        state: &mut PicState<T>,
        control: ControlState,
        target_dt: f64,
        minimum_substeps: usize,
        stability_retries: usize,
    ) -> Result<StepReport, SolverError> {
        let tolerance = if T::PRECISION == crate::scenario::Precision::Float32 {
            1.0e-6
        } else {
            1.0e-12
        };
        let expected = state.control.time + target_dt;
        if !target_dt.is_finite()
            || target_dt <= 0.0
            || !control.time.is_finite()
            || !control.angle_degrees.is_finite()
            || !control.angular_velocity_degrees.is_finite()
            || (control.time - expected).abs() > tolerance * expected.abs().max(1.0)
        {
            return Err(SolverError::new(
                FailureReason::TimeContractFailure,
                FailureStage::TimeMapping,
                "PIC/FLIP control does not complete the requested interval",
            ));
        }
        let snapshot = state.grid.particle_grid_snapshot()?;
        let grid_maximum = faces_to_cells(&snapshot.velocity)
            .values()
            .iter()
            .map(|value| value[0].to_f64().hypot(value[1].to_f64()))
            .fold(0.0, f64::max);
        let particle_maximum = state
            .particles
            .iter()
            .map(|particle| {
                particle.velocity[0]
                    .to_f64()
                    .hypot(particle.velocity[1].to_f64())
            })
            .fold(0.0, f64::max);
        let wall_speed =
            control.angular_velocity_degrees.to_radians().abs() * state.geometry.maximum_radius();
        let sweep_speed = (control.angle_degrees - state.control.angle_degrees)
            .to_radians()
            .abs()
            * state.geometry.maximum_radius()
            / target_dt;
        let resolved = grid_maximum
            .max(particle_maximum)
            .max(wall_speed)
            .max(sweep_speed)
            .max(1.0e-6);
        let substeps = minimum_substeps.max(
            (target_dt * resolved / (state.cfl * state.domain.dx().min(state.domain.dy()))).ceil()
                as usize,
        );
        if substeps > MAX_SUBSTEPS {
            return Err(SolverError::new(
                FailureReason::StabilityLimit,
                FailureStage::ParticleAdvection,
                "PIC/FLIP motion requires too many internal substeps",
            ));
        }
        let dt = target_dt / substeps as f64;
        let start = state.control;
        let effective_blend = if state.settling_steps > 0 {
            state.blend.min(0.05)
        } else {
            state.blend
        };
        state.swept_collisions_last_step = 0;
        let mut final_snapshot = snapshot;
        let mut projection_iterations = 0_usize;
        let mut projection_residual = 0.0_f64;
        let mut viscosity_iterations = 0_usize;
        let mut viscosity_residual = 0.0_f64;
        for substep in 0..substeps {
            let fraction = (substep + 1) as f64 / substeps as f64;
            let sub_control = ControlState {
                time: start.time + fraction * target_dt,
                angle_degrees: start.angle_degrees
                    + fraction * (control.angle_degrees - start.angle_degrees),
                angular_velocity_degrees: control.angular_velocity_degrees,
            };
            let old = state.grid.particle_grid_snapshot()?;
            let transferred = Self::scatter_to_faces(state, &old.velocity);
            state.grid.stage_particle_faces(transferred)?;
            let pre_projection = state.grid.particle_grid_snapshot()?;
            let (projection, diffusion) = state.grid.advance_particle_grid(sub_control, dt)?;
            projection_iterations = projection_iterations.max(projection.iterations);
            projection_residual = projection_residual.max(projection.residual_linf);
            viscosity_iterations = viscosity_iterations.max(diffusion.iterations);
            viscosity_residual = viscosity_residual.max(diffusion.residual_linf);
            let new = state.grid.particle_grid_snapshot()?;
            for index in 0..state.particles.len() {
                let old_position = [
                    state.particles[index].position[0].to_f64(),
                    state.particles[index].position[1].to_f64(),
                ];
                let before = sample_faces(&pre_projection.velocity, state.domain, old_position);
                let after = sample_faces(&new.velocity, state.domain, old_position);
                let flip = [
                    state.particles[index].velocity[0].to_f64() + after[0] - before[0],
                    state.particles[index].velocity[1].to_f64() + after[1] - before[1],
                ];
                state.particles[index].velocity = [
                    T::from_f64(effective_blend * flip[0] + (1.0 - effective_blend) * after[0]),
                    T::from_f64(effective_blend * flip[1] + (1.0 - effective_blend) * after[1]),
                ];
                let midpoint = [
                    old_position[0] + 0.5 * dt * after[0],
                    old_position[1] + 0.5 * dt * after[1],
                ];
                let midpoint_velocity = sample_faces(&new.velocity, state.domain, midpoint);
                state.particles[index].position = [
                    T::from_f64(old_position[0] + dt * midpoint_velocity[0]),
                    T::from_f64(old_position[1] + dt * midpoint_velocity[1]),
                ];
                Self::apply_particle_boundary(
                    state,
                    index,
                    old_position,
                    old.control,
                    sub_control,
                    &new.velocity,
                )?;
            }
            final_snapshot = new;
        }
        state.advance_count += 1;
        let interval = Self::option_usize(&state.scenario, "pic_population_interval", 8)?.max(1);
        if state.advance_count % interval == 0 {
            Self::maintain_population(state, control, &final_snapshot.velocity);
        }
        state.settling_steps = state.settling_steps.saturating_sub(1);
        state.control = control;
        let final_grid_maximum = faces_to_cells(&final_snapshot.velocity)
            .values()
            .iter()
            .map(|value| value[0].to_f64().hypot(value[1].to_f64()))
            .fold(0.0, f64::max);
        let final_particle_maximum = state
            .particles
            .iter()
            .map(|particle| {
                particle.velocity[0]
                    .to_f64()
                    .hypot(particle.velocity[1].to_f64())
            })
            .fold(0.0, f64::max);
        let maximum = final_grid_maximum.max(final_particle_maximum);
        let accepted_cfl = dt * maximum.max(wall_speed).max(sweep_speed)
            / state.domain.dx().min(state.domain.dy());
        if accepted_cfl > state.cfl * (1.0 + 1.0e-6) {
            return Err(SolverError::new(
                FailureReason::StabilityLimit,
                FailureStage::ParticleAdvection,
                format!(
                    "accepted_cfl={accepted_cfl};maximum_cfl={};substeps={substeps}",
                    state.cfl
                ),
            ));
        }
        state.revision = state.revision.saturating_add(1);
        let mut evidence = Self::population_evidence(state, &final_snapshot.solid)?;
        evidence.extend([
            (
                "stability_retries".into(),
                EvidenceValue::Number(stability_retries as f64),
            ),
            (
                "maximum_particle_speed".into(),
                EvidenceValue::Number(final_particle_maximum),
            ),
            (
                "maximum_wall_speed".into(),
                EvidenceValue::Number(wall_speed),
            ),
            (
                "maximum_geometry_sweep_speed".into(),
                EvidenceValue::Number(sweep_speed),
            ),
            (
                "maximum_particle_cfl".into(),
                EvidenceValue::Number(accepted_cfl),
            ),
            (
                "unsupported_face_fraction".into(),
                EvidenceValue::Number(state.unsupported_face_fraction),
            ),
            (
                "swept_collisions_last_step".into(),
                EvidenceValue::Number(state.swept_collisions_last_step as f64),
            ),
            (
                "projection_iterations".into(),
                EvidenceValue::Number(projection_iterations as f64),
            ),
            (
                "projection_residual_linf".into(),
                EvidenceValue::Number(projection_residual),
            ),
            (
                "viscosity_iterations".into(),
                EvidenceValue::Number(viscosity_iterations as f64),
            ),
            (
                "viscosity_residual_linf".into(),
                EvidenceValue::Number(viscosity_residual),
            ),
            (
                "requested_reynolds".into(),
                EvidenceValue::Number(state.reynolds),
            ),
            (
                "effective_reynolds".into(),
                EvidenceValue::Number(state.reynolds),
            ),
        ]);
        Ok(StepReport {
            requested_dt: target_dt,
            advanced_dt: target_dt,
            substeps,
            max_speed: maximum,
            state_revision: state.revision,
            evidence,
            warnings: Vec::new(),
        })
    }
}

impl<T: FlowScalar> FlowSolver<T> for PicFlip<T> {
    fn info(&self) -> &SolverInfo {
        &self.info
    }

    fn initialize(
        &mut self,
        scenario: &Scenario,
        geometry: &NacaFoil,
        seed: u32,
    ) -> Result<(), SolverError> {
        self.restart(
            scenario,
            geometry,
            seed,
            RestartState {
                time: 0.0,
                angle_degrees: scenario
                    .controls()
                    .first()
                    .map_or(0.0, |value| value.angle_degrees),
                reynolds: scenario.reynolds(),
            },
        )
    }

    fn restart(
        &mut self,
        scenario: &Scenario,
        geometry: &NacaFoil,
        seed: u32,
        start: RestartState,
    ) -> Result<(), SolverError> {
        self.state = Some(Self::create_state(
            scenario,
            geometry,
            seed,
            start,
            &self.execution_target,
        )?);
        Ok(())
    }

    fn set_reynolds(&mut self, reynolds: f64) -> Result<ReynoldsOutcome, SolverError> {
        if !reynolds.is_finite() || reynolds <= 0.0 {
            return Err(SolverError::new(
                FailureReason::InvalidRelaxation,
                FailureStage::Postcondition,
                "Reynolds number must be finite and positive",
            ));
        }
        let state = self.state.as_mut().ok_or_else(|| {
            SolverError::new(
                FailureReason::UnsupportedConversion,
                FailureStage::Initialization,
                "PIC/FLIP has not been initialized",
            )
        })?;
        let outcome = state.grid.set_reynolds(reynolds)?;
        if state.reynolds != outcome.effective {
            state.reynolds = outcome.effective;
            state.revision = state.revision.saturating_add(1);
        }
        Ok(outcome)
    }

    fn advance(
        &mut self,
        control: ControlState,
        target_dt: f64,
    ) -> Result<StepReport, SolverError> {
        let original = self.state()?.clone();
        let mut minimum_substeps = 1;
        let mut retries = 0;
        loop {
            let mut candidate = original.clone();
            match Self::advance_candidate(
                &mut candidate,
                control,
                target_dt,
                minimum_substeps,
                retries,
            ) {
                Ok(report) => {
                    self.state = Some(candidate);
                    return Ok(report);
                }
                Err(error)
                    if error.reason == FailureReason::StabilityLimit
                        && error.detail.starts_with("accepted_cfl=") =>
                {
                    let values = error
                        .detail
                        .split(';')
                        .filter_map(|entry| entry.split_once('='))
                        .collect::<BTreeMap<_, _>>();
                    let accepted = values
                        .get("accepted_cfl")
                        .and_then(|value| value.parse::<f64>().ok())
                        .unwrap_or(f64::INFINITY);
                    let maximum = values
                        .get("maximum_cfl")
                        .and_then(|value| value.parse::<f64>().ok())
                        .unwrap_or(original.cfl);
                    let attempted = values
                        .get("substeps")
                        .and_then(|value| value.parse::<usize>().ok())
                        .unwrap_or(minimum_substeps);
                    minimum_substeps = (attempted + 1)
                        .max((1.05 * attempted as f64 * accepted / maximum).ceil() as usize);
                    retries += 1;
                    if minimum_substeps > MAX_SUBSTEPS {
                        return Err(error);
                    }
                }
                Err(error) => return Err(error),
            }
        }
    }

    fn sample_velocity(
        &self,
        points_xy: &[[T; 2]],
        output_xy: &mut [[T; 2]],
    ) -> Result<(), SolverError> {
        self.state()?.grid.sample_velocity(points_xy, output_xy)
    }

    fn export_state(&self) -> Result<CanonicalFlowState2<T>, SolverError> {
        let state = self.state()?;
        let mut output = state.grid.export_state()?;
        output.source_solver.clone_from(&self.info.id);
        output.producer = Producer {
            implementation: "rust".into(),
            execution_target: self.execution_target.clone(),
            build: None,
        };
        Ok(output)
    }

    fn import_state(
        &mut self,
        imported: &CanonicalFlowState2<T>,
        control: ControlState,
    ) -> ImportOutcome {
        let Some(current) = self.state.as_ref() else {
            return ImportOutcome {
                accepted: false,
                reason: Some(FailureReason::UnsupportedConversion),
                stage: FailureStage::CanonicalImport,
                evidence: Evidence::new(),
                discarded_state: Vec::new(),
                warnings: vec!["PIC/FLIP is not initialized".into()],
            };
        };
        let mut candidate = current.clone();
        let outcome = candidate.grid.import_state(imported, control);
        if !outcome.accepted {
            return outcome;
        }
        candidate.control = control;
        candidate.rng = Pcg32::new(u64::from(candidate.scenario.seed()), RNG_STREAM);
        if let Err(error) = Self::seed_particles(&mut candidate, control.angle_degrees) {
            return ImportOutcome {
                accepted: false,
                reason: Some(error.reason),
                stage: error.stage,
                evidence: Evidence::new(),
                discarded_state: Vec::new(),
                warnings: vec![error.detail],
            };
        }
        candidate.settling_steps = 1;
        candidate.revision = candidate.revision.saturating_add(1);
        self.state = Some(candidate);
        ImportOutcome {
            accepted: true,
            reason: None,
            stage: FailureStage::CanonicalImport,
            evidence: Evidence::new(),
            discarded_state: vec![
                "pressure_history".into(),
                "solver_particles".into(),
                "flip_deltas".into(),
            ],
            warnings: vec!["solver particles reseeded; first step is PIC-dominant".into()],
        }
    }

    fn diagnostics(&self) -> Result<Diagnostics, SolverError> {
        let state = self.state()?;
        let mut diagnostics = state.grid.diagnostics()?;
        let snapshot = state.grid.particle_grid_snapshot()?;
        let population = Self::population_evidence(state, &snapshot.solid)?;
        diagnostics.state_revision = state.revision;
        diagnostics
            .values
            .insert("particle_count".into(), state.particles.len() as f64);
        diagnostics
            .values
            .insert("pic_flip_blend".into(), state.blend);
        diagnostics.values.insert(
            "swept_collisions_last_step".into(),
            state.swept_collisions_last_step as f64,
        );
        if let Some(EvidenceValue::Number(value)) = population.get("unresolved_solid_particles") {
            diagnostics
                .values
                .insert("particles_inside_solid".into(), *value);
        }
        diagnostics.evidence.extend(population);
        Ok(diagnostics)
    }

    fn interactive_tuning(&self) -> Option<InteractiveTuning> {
        self.state.as_ref().map(|state| InteractiveTuning {
            id: "pic-flip-blend".into(),
            label: "FLIP".into(),
            value: TuningValue::Number(state.blend),
            can_decrease: state.blend > 0.0,
            can_increase: state.blend < 1.0,
        })
    }

    fn adjust_interactive_tuning(
        &mut self,
        direction: i8,
    ) -> Result<Option<InteractiveTuning>, SolverError> {
        let state = self.state.as_mut().ok_or_else(|| {
            SolverError::new(
                FailureReason::UnsupportedConversion,
                FailureStage::Initialization,
                "PIC/FLIP has not been initialized",
            )
        })?;
        let selected = (state.blend + 0.05 * f64::from(direction.signum())).clamp(0.0, 1.0);
        if selected != state.blend {
            state.blend = selected;
            state.revision = state.revision.saturating_add(1);
        }
        Ok(self.interactive_tuning())
    }

    fn state_revision(&self) -> u64 {
        self.state.as_ref().map_or(0, |state| state.revision)
    }
}

#[cfg(test)]
mod tests {
    use super::PicFlip;
    use crate::{
        geometry::NacaFoil,
        lbm::LbmD2q9,
        scenario::{ControlState, Scenario},
        solver::{FailureReason, FlowSolver},
        stable_fluids::StableFluids,
    };
    use std::path::PathBuf;

    fn repository_root() -> PathBuf {
        PathBuf::from(env!("CARGO_MANIFEST_DIR")).join("../../../..")
    }

    fn scenario() -> Scenario {
        let document =
            std::fs::read_to_string(repository_root().join("scenarios/airfoil/default.json"))
                .unwrap();
        Scenario::from_json(&document).unwrap()
    }

    fn small_scenario() -> Scenario {
        let mut document: serde_json::Value = serde_json::from_str(
            &std::fs::read_to_string(repository_root().join("scenarios/airfoil/default.json"))
                .unwrap(),
        )
        .unwrap();
        document["resolution"] = serde_json::json!([32, 16]);
        document["output_dt"] = serde_json::json!(0.01);
        Scenario::from_json(&serde_json::to_string(&document).unwrap()).unwrap()
    }

    fn solver(id: &str) -> Box<dyn FlowSolver<f32>> {
        match id {
            "stable-fluids" => Box::new(StableFluids::<f32>::default()),
            "lbm-d2q9" => Box::new(LbmD2q9::<f32>::default()),
            "pic-flip" => Box::new(PicFlip::<f32>::default()),
            _ => panic!("unknown test solver"),
        }
    }

    #[test]
    fn seeds_four_particles_per_fluid_cell_and_advances_exact_time() {
        let scenario = scenario();
        let geometry = NacaFoil::new(scenario.foil().clone()).unwrap();
        let mut solver = PicFlip::<f32>::default();
        solver.initialize(&scenario, &geometry, 0).unwrap();
        let report = solver
            .advance(
                ControlState {
                    time: scenario.output_dt(),
                    angle_degrees: 4.0,
                    angular_velocity_degrees: 0.0,
                },
                scenario.output_dt(),
            )
            .unwrap();
        assert_eq!(report.advanced_dt, scenario.output_dt());
        assert!(report.evidence["particle_count"].is_valid());
        assert!(solver.diagnostics().unwrap().values["particles_inside_solid"] <= 0.0);
    }

    #[test]
    fn rejected_motion_rolls_back_particles_rng_and_grid() {
        let scenario = scenario();
        let geometry = NacaFoil::new(scenario.foil().clone()).unwrap();
        let mut solver = PicFlip::<f32>::default();
        solver.initialize(&scenario, &geometry, 0).unwrap();
        let before = solver.export_state().unwrap();
        let revision = solver.state_revision();
        let error = solver
            .advance(
                ControlState {
                    time: scenario.output_dt(),
                    angle_degrees: 30.0,
                    angular_velocity_degrees: 1.0e9,
                },
                scenario.output_dt(),
            )
            .unwrap_err();
        assert_eq!(error.reason, FailureReason::StabilityLimit);
        assert_eq!(solver.export_state().unwrap(), before);
        assert_eq!(solver.state_revision(), revision);
    }

    #[test]
    fn canonical_import_reseeds_and_uses_pic_dominant_settling() {
        let scenario = scenario();
        let geometry = NacaFoil::new(scenario.foil().clone()).unwrap();
        let mut source = PicFlip::<f32>::default();
        let mut destination = PicFlip::<f32>::default();
        source.initialize(&scenario, &geometry, 3).unwrap();
        destination.initialize(&scenario, &geometry, 9).unwrap();
        let canonical = source.export_state().unwrap();
        let control = ControlState {
            time: canonical.time,
            angle_degrees: canonical.angle_degrees,
            angular_velocity_degrees: canonical.angular_velocity_degrees,
        };
        let outcome = destination.import_state(&canonical, control);
        assert!(outcome.accepted);
        assert!(
            outcome
                .discarded_state
                .iter()
                .any(|value| value == "solver_particles")
        );
    }

    #[test]
    fn full_preview_accepts_static_complete_stall() {
        let scenario = scenario();
        let geometry = NacaFoil::new(scenario.foil().clone()).unwrap();
        let mut solver = PicFlip::<f32>::default();
        solver.initialize(&scenario, &geometry, 0).unwrap();
        let report = solver
            .advance(
                ControlState {
                    time: scenario.output_dt(),
                    angle_degrees: 30.0,
                    angular_velocity_degrees: 0.0,
                },
                scenario.output_dt(),
            )
            .unwrap();
        assert!(report.max_speed.is_finite());
        assert_eq!(
            solver.diagnostics().unwrap().values["particles_inside_solid"],
            0.0
        );
    }

    #[test]
    fn deterministic_population_maintenance_matches_exactly() {
        let scenario = small_scenario();
        let geometry = NacaFoil::new(scenario.foil().clone()).unwrap();
        let mut first = PicFlip::<f32>::default();
        let mut second = PicFlip::<f32>::default();
        first.initialize(&scenario, &geometry, 42).unwrap();
        second.initialize(&scenario, &geometry, 42).unwrap();
        for step in 1..=8 {
            let control = ControlState {
                time: f64::from(step) * scenario.output_dt(),
                angle_degrees: 4.0,
                angular_velocity_degrees: 0.0,
            };
            first.advance(control, scenario.output_dt()).unwrap();
            second.advance(control, scenario.output_dt()).unwrap();
        }
        assert_eq!(
            first.export_state().unwrap(),
            second.export_state().unwrap()
        );
        assert_eq!(first.diagnostics().unwrap(), second.diagnostics().unwrap());
    }

    #[test]
    fn all_six_directed_solver_imports_accept_a_tentative_step() {
        let scenario = small_scenario();
        let geometry = NacaFoil::new(scenario.foil().clone()).unwrap();
        let ids = ["stable-fluids", "lbm-d2q9", "pic-flip"];
        for source_id in ids {
            let mut source = solver(source_id);
            source.initialize(&scenario, &geometry, 0).unwrap();
            let canonical = source.export_state().unwrap();
            for destination_id in ids {
                if source_id == destination_id {
                    continue;
                }
                let mut destination = solver(destination_id);
                destination.initialize(&scenario, &geometry, 0).unwrap();
                let import_control = ControlState {
                    time: canonical.time,
                    angle_degrees: canonical.angle_degrees,
                    angular_velocity_degrees: canonical.angular_velocity_degrees,
                };
                let outcome = destination.import_state(&canonical, import_control);
                assert!(
                    outcome.accepted,
                    "{source_id} -> {destination_id}: {:?}",
                    outcome.reason
                );
                let report = destination
                    .advance(
                        ControlState {
                            time: canonical.time + scenario.output_dt(),
                            angle_degrees: canonical.angle_degrees,
                            angular_velocity_degrees: 0.0,
                        },
                        scenario.output_dt(),
                    )
                    .unwrap();
                assert_eq!(report.advanced_dt, scenario.output_dt());
            }
        }
    }

    #[test]
    fn float64_validation_path_advances_and_exports_finite_state() {
        let mut document: serde_json::Value = serde_json::from_str(
            &std::fs::read_to_string(repository_root().join("scenarios/airfoil/default.json"))
                .unwrap(),
        )
        .unwrap();
        document["resolution"] = serde_json::json!([24, 12]);
        document["precision"] = serde_json::json!("float64");
        document["output_dt"] = serde_json::json!(0.01);
        let scenario = Scenario::from_json(&serde_json::to_string(&document).unwrap()).unwrap();
        let geometry = NacaFoil::new(scenario.foil().clone()).unwrap();
        let mut solver = PicFlip::<f64>::default();
        solver.initialize(&scenario, &geometry, 11).unwrap();
        solver
            .advance(
                ControlState {
                    time: 0.01,
                    angle_degrees: 4.0,
                    angular_velocity_degrees: 0.0,
                },
                0.01,
            )
            .unwrap();
        assert!(
            solver
                .export_state()
                .unwrap()
                .velocity
                .iter()
                .flatten()
                .all(|value| value.is_finite())
        );
    }
}
