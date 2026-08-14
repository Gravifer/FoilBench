//! Native/WASM shared D2Q9 two-relaxation-time lattice Boltzmann solver.

#![allow(
    clippy::cast_possible_truncation,
    clippy::cast_possible_wrap,
    clippy::cast_precision_loss,
    clippy::cast_sign_loss,
    clippy::float_cmp,
    clippy::missing_panics_doc,
    clippy::needless_range_loop,
    clippy::too_many_lines
)]

use std::collections::BTreeMap;

use crate::{
    canonical::{CanonicalGeometryDescriptor, Producer},
    field::{ScalarField2, VectorField2},
    geometry::NacaFoil,
    grid::{GridDomain2, cells_to_faces, sample_cells},
    metrics::compute_flow_metrics,
    raster::rasterize_geometry,
    scenario::{ControlState, Precision, Scenario},
    solver::{
        CanonicalFlowState2, Diagnostics, Evidence, EvidenceValue, FailureReason, FailureStage,
        FlowScalar, FlowSolver, ImportOutcome, RestartState, ReynoldsOutcome, SolverError,
        SolverInfo, StepReport,
    },
};

const CX: [isize; 9] = [0, 1, 0, -1, 0, 1, -1, -1, 1];
const CY: [isize; 9] = [0, 0, 1, 0, -1, 1, 1, -1, -1];
const WEIGHT: [f64; 9] = [
    4.0 / 9.0,
    1.0 / 9.0,
    1.0 / 9.0,
    1.0 / 9.0,
    1.0 / 9.0,
    1.0 / 36.0,
    1.0 / 36.0,
    1.0 / 36.0,
    1.0 / 36.0,
];
const OPPOSITE: [usize; 9] = [0, 3, 4, 1, 2, 7, 8, 5, 6];
const LATTICE_SOUND_SPEED: f64 = 0.577_350_269_189_625_8;
const MAXIMUM_MACH: f64 = 0.08;
const MAXIMUM_LATTICE_SPEED: f64 = MAXIMUM_MACH * LATTICE_SOUND_SPEED;
const MAXIMUM_SUBSTEPS: usize = 512;
const MINIMUM_POPULATION: f64 = -1.0e-6;

/// Revision 5 convective outlet update for one population.
#[must_use]
pub fn convective_outlet_population(previous: f64, interior: f64, lattice_speed: f64) -> f64 {
    previous + lattice_speed * (interior - previous)
}

/// Revision 5 quadratic sponge strength at one x-major cell.
#[must_use]
pub fn lbm_sponge_strength(
    nx: usize,
    ny: usize,
    x: usize,
    y: usize,
    periodic_x: bool,
    periodic_y: bool,
    channel_walls: bool,
) -> f64 {
    let width = 3_usize.max(nx.min(ny) / 16);
    let transverse = if periodic_y || channel_walls {
        0.0
    } else {
        let distance = y.min(ny - 1 - y);
        0.12 * ((width.saturating_sub(distance)) as f64 / width as f64).powi(2)
    };
    let outlet = if periodic_x {
        0.0
    } else {
        let outlet_width = 2 * width;
        let distance = nx - 1 - x;
        0.08 * ((outlet_width.saturating_sub(distance)) as f64 / outlet_width as f64).powi(2)
    };
    transverse.max(outlet)
}

#[derive(Clone)]
struct LbmState<T: FlowScalar> {
    scenario: Scenario,
    geometry: NacaFoil,
    domain: GridDomain2,
    populations: Vec<T>,
    scratch: Vec<T>,
    density: Vec<f64>,
    lattice_velocity: Vec<[f64; 2]>,
    outlet: Vec<T>,
    sponge: ScalarField2<f64>,
    solid: ScalarField2<u8>,
    previous_solid: ScalarField2<u8>,
    solid_angle_degrees: f64,
    control: ControlState,
    reynolds: f64,
    effective_reynolds: f64,
    reference_speed: f64,
    lattice_speed: f64,
    omega_plus: f64,
    omega_minus: f64,
    revision: u64,
    last_substeps: usize,
    stability_retries: usize,
}

#[derive(Clone)]
pub struct LbmD2q9<T: FlowScalar> {
    info: SolverInfo,
    execution_target: String,
    state: Option<LbmState<T>>,
}

impl<T: FlowScalar> Default for LbmD2q9<T> {
    fn default() -> Self {
        Self::new("native")
    }
}

impl<T: FlowScalar> LbmD2q9<T> {
    #[must_use]
    pub fn new(execution_target: impl Into<String>) -> Self {
        Self {
            info: SolverInfo {
                id: "lbm-d2q9".into(),
                display_name: "D2Q9 TRT LBM".into(),
                dimensions: vec![2],
                supports_moving_boundary: true,
                supported_precisions: vec![Precision::Float32, Precision::Float64],
                acceleration: "Rust deterministic TRT collide/stream".into(),
            },
            execution_target: execution_target.into(),
            state: None,
        }
    }

    #[must_use]
    pub fn current_reynolds(&self) -> Option<f64> {
        self.state.as_ref().map(|state| state.reynolds)
    }

    fn state(&self) -> Result<&LbmState<T>, SolverError> {
        self.state.as_ref().ok_or_else(|| {
            SolverError::new(
                FailureReason::UnsupportedConversion,
                FailureStage::Initialization,
                "D2Q9 LBM has not been initialized",
            )
        })
    }

    fn geometry_descriptor(geometry: &NacaFoil) -> CanonicalGeometryDescriptor {
        CanonicalGeometryDescriptor {
            family: "naca-four-digit-v1".into(),
            naca: geometry.descriptor().naca.clone(),
            chord: geometry.descriptor().chord,
            pivot: geometry.descriptor().pivot.clone(),
        }
    }

    fn is_poiseuille(scenario: &Scenario) -> bool {
        scenario
            .solver_options()
            .get("initial_condition")
            .and_then(serde_json::Value::as_str)
            == Some("poiseuille")
    }

    fn equilibrium(direction: usize, density: f64, velocity: [f64; 2]) -> f64 {
        let cu = CX[direction] as f64 * velocity[0] + CY[direction] as f64 * velocity[1];
        let speed2 = velocity[0].mul_add(velocity[0], velocity[1] * velocity[1]);
        WEIGHT[direction] * density * (1.0 + 3.0 * cu + 4.5 * cu * cu - 1.5 * speed2)
    }

    fn population_index(state: &LbmState<T>, direction: usize, cell: usize) -> usize {
        direction * state.domain.nx() * state.domain.ny() + cell
    }

    fn initial_velocity(scenario: &Scenario, domain: GridDomain2) -> VectorField2<T> {
        let mut output = VectorField2::filled(
            domain.nx(),
            domain.ny(),
            [
                T::from_f64(scenario.freestream()[0]),
                T::from_f64(scenario.freestream()[1]),
            ],
        );
        let initial = scenario
            .solver_options()
            .get("initial_condition")
            .and_then(serde_json::Value::as_str)
            .unwrap_or("freestream");
        for y in 0..domain.ny() {
            for x in 0..domain.nx() {
                let [px, py] = domain.cell_center(x, y);
                let velocity = match initial {
                    "taylor-green" => [px.sin() * py.cos(), -px.cos() * py.sin()],
                    "poiseuille" => {
                        let center = 0.5 * (domain.bounds[1][0] + domain.bounds[1][1]);
                        let radius = 0.5 * (domain.bounds[1][1] - domain.bounds[1][0]);
                        [1.5 * (1.0 - ((py - center) / radius).powi(2)), 0.0]
                    }
                    _ => [scenario.freestream()[0], scenario.freestream()[1]],
                };
                output.set(x, y, [T::from_f64(velocity[0]), T::from_f64(velocity[1])]);
            }
        }
        output
    }

    fn reference_speed(scenario: &Scenario) -> f64 {
        let speed = scenario.freestream()[0].hypot(scenario.freestream()[1]);
        if Self::is_poiseuille(scenario)
            || scenario
                .solver_options()
                .get("initial_condition")
                .and_then(serde_json::Value::as_str)
                == Some("taylor-green")
        {
            speed.max(1.0)
        } else {
            speed.max(1.0e-6)
        }
    }

    fn solid_at(
        geometry: &NacaFoil,
        domain: GridDomain2,
        control: ControlState,
    ) -> ScalarField2<u8> {
        rasterize_geometry::<T>(geometry, domain, control).solid
    }

    fn sponge(domain: GridDomain2, channel_walls: bool) -> ScalarField2<f64> {
        let mut output = ScalarField2::filled(domain.nx(), domain.ny(), 0.0);
        for y in 0..domain.ny() {
            for x in 0..domain.nx() {
                output.set(
                    x,
                    y,
                    lbm_sponge_strength(
                        domain.nx(),
                        domain.ny(),
                        x,
                        y,
                        domain.periodic_x,
                        domain.periodic_y,
                        channel_walls,
                    ),
                );
            }
        }
        output
    }

    fn configure_relaxation(state: &mut LbmState<T>) -> Result<(), SolverError> {
        let chord_cells = state.geometry.descriptor().chord / state.domain.dx();
        let requested_viscosity = state.lattice_speed * chord_cells / state.reynolds;
        let viscosity = requested_viscosity.max((0.52 - 0.5) / 3.0);
        let tau_plus = 0.5 + 3.0 * viscosity;
        let tau_minus = 0.5 + (3.0 / 16.0) / (tau_plus - 0.5).max(1.0e-6);
        state.omega_plus = 1.0 / tau_plus;
        state.omega_minus = 1.0 / tau_minus;
        state.effective_reynolds = state.lattice_speed * chord_cells / viscosity;
        if !(state.omega_plus > 0.0
            && state.omega_plus < 2.0
            && state.omega_minus > 0.0
            && state.omega_minus < 2.0)
        {
            return Err(SolverError::new(
                FailureReason::InvalidRelaxation,
                FailureStage::Collision,
                "TRT relaxation frequency left (0, 2)",
            ));
        }
        Ok(())
    }

    fn refresh_macroscopic(state: &mut LbmState<T>) {
        let count = state.domain.nx() * state.domain.ny();
        for cell in 0..count {
            let mut rho = 0.0;
            let mut momentum = [0.0, 0.0];
            for direction in 0..9 {
                let value =
                    state.populations[Self::population_index(state, direction, cell)].to_f64();
                rho += value;
                momentum[0] += value * CX[direction] as f64;
                momentum[1] += value * CY[direction] as f64;
            }
            state.density[cell] = rho;
            let divisor = rho.max(1.0e-12);
            state.lattice_velocity[cell] = [momentum[0] / divisor, momentum[1] / divisor];
        }
    }

    fn physical_velocity(state: &LbmState<T>) -> VectorField2<T> {
        let scale = state.reference_speed / state.lattice_speed;
        let mut output = VectorField2::filled(
            state.domain.nx(),
            state.domain.ny(),
            [T::from_f64(0.0), T::from_f64(0.0)],
        );
        for y in 0..state.domain.ny() {
            for x in 0..state.domain.nx() {
                if state.solid.get(x, y) == 0 {
                    let value = state.lattice_velocity[y * state.domain.nx() + x];
                    output.set(
                        x,
                        y,
                        [T::from_f64(value[0] * scale), T::from_f64(value[1] * scale)],
                    );
                }
            }
        }
        output
    }

    fn capture_outlet(state: &mut LbmState<T>) {
        let nx = state.domain.nx();
        let ny = state.domain.ny();
        let count = nx * ny;
        state.outlet.resize(9 * ny, T::from_f64(0.0));
        for direction in 0..9 {
            for y in 0..ny {
                state.outlet[direction * ny + y] =
                    state.populations[direction * count + y * nx + nx - 1];
            }
        }
    }

    fn rescale_populations(state: &mut LbmState<T>, selected_speed: f64) {
        if state.populations.is_empty() || (selected_speed - state.lattice_speed).abs() < 1.0e-14 {
            return;
        }
        let count = state.domain.nx() * state.domain.ny();
        Self::refresh_macroscopic(state);
        let ratio = selected_speed / state.lattice_speed;
        let mut output = vec![T::from_f64(0.0); state.populations.len()];
        for cell in 0..count {
            let old = state.lattice_velocity[cell];
            let new = [old[0] * ratio, old[1] * ratio];
            for direction in 0..9 {
                let index = direction * count + cell;
                let old_equilibrium = Self::equilibrium(direction, state.density[cell], old);
                let new_equilibrium = Self::equilibrium(direction, state.density[cell], new);
                output[index] = T::from_f64(
                    new_equilibrium + ratio * (state.populations[index].to_f64() - old_equilibrium),
                );
            }
        }
        state.populations = output;
        state.scratch.fill(T::from_f64(0.0));
        Self::refresh_macroscopic(state);
        Self::capture_outlet(state);
    }

    fn configure_temporal_scaling(
        state: &mut LbmState<T>,
        target_dt: f64,
        maximum_physical_speed: f64,
        minimum_substeps: usize,
    ) -> Result<usize, SolverError> {
        let required = (target_dt * maximum_physical_speed.max(state.reference_speed)
            / (MAXIMUM_LATTICE_SPEED * state.domain.dx())
            - 1.0e-12)
            .ceil()
            .max(1.0) as usize;
        let substeps = required.max(minimum_substeps);
        if substeps > MAXIMUM_SUBSTEPS {
            return Err(SolverError::new(
                FailureReason::ExcessiveVelocity,
                FailureStage::TimeMapping,
                format!("LBM requires {substeps} substeps to respect its Mach limit"),
            ));
        }
        let selected_speed =
            state.reference_speed * target_dt / (substeps as f64 * state.domain.dx());
        Self::rescale_populations(state, selected_speed);
        state.lattice_speed = selected_speed;
        Self::configure_relaxation(state)?;
        Ok(substeps)
    }

    fn configure_import_scaling(
        state: &mut LbmState<T>,
        maximum_physical_speed: f64,
    ) -> Result<(), SolverError> {
        state.lattice_speed = MAXIMUM_LATTICE_SPEED * state.reference_speed
            / maximum_physical_speed.max(state.reference_speed);
        Self::configure_relaxation(state)
    }

    fn wall_lattice_velocity(
        state: &LbmState<T>,
        point: [f64; 2],
        control: ControlState,
    ) -> [f64; 2] {
        let omega = control.angular_velocity_degrees.to_radians();
        let pivot = state.geometry.descriptor().pivot.as_slice();
        let scale = state.lattice_speed / state.reference_speed;
        [
            -omega * (point[1] - pivot[1]) * scale,
            omega * (point[0] - pivot[0]) * scale,
        ]
    }

    fn apply_boundaries(state: &mut LbmState<T>) {
        let nx = state.domain.nx();
        let ny = state.domain.ny();
        let count = nx * ny;
        let channel_walls = Self::is_poiseuille(&state.scenario);
        let target = [
            state.scenario.freestream()[0] * state.lattice_speed / state.reference_speed,
            state.scenario.freestream()[1] * state.lattice_speed / state.reference_speed,
        ];
        if !state.domain.periodic_x {
            for y in 0..ny {
                for direction in 0..9 {
                    state.populations[direction * count + y * nx] =
                        T::from_f64(Self::equilibrium(direction, 1.0, target));
                    let outlet_index = direction * count + y * nx + nx - 1;
                    let previous = state.outlet[direction * ny + y].to_f64();
                    let interior = state.populations[direction * count + y * nx + nx - 2].to_f64();
                    state.populations[outlet_index] = T::from_f64(convective_outlet_population(
                        previous,
                        interior,
                        state.lattice_speed,
                    ));
                }
            }
        }
        if !state.domain.periodic_y && !channel_walls {
            for x in 0..nx {
                for direction in 0..9 {
                    let equilibrium = T::from_f64(Self::equilibrium(direction, 1.0, target));
                    state.populations[direction * count + x] = equilibrium;
                    state.populations[direction * count + (ny - 1) * nx + x] = equilibrium;
                }
            }
        }
        for y in 0..ny {
            for x in 0..nx {
                let strength = state.sponge.get(x, y);
                if strength <= 0.0 || state.solid.get(x, y) != 0 {
                    continue;
                }
                let cell = y * nx + x;
                for direction in 0..9 {
                    let index = direction * count + cell;
                    let equilibrium = Self::equilibrium(direction, 1.0, target);
                    state.populations[index] = T::from_f64(
                        (1.0 - strength) * state.populations[index].to_f64()
                            + strength * equilibrium,
                    );
                }
            }
        }
        Self::capture_outlet(state);
    }

    fn lattice_step(state: &mut LbmState<T>, control: ControlState) {
        let nx = state.domain.nx();
        let ny = state.domain.ny();
        let count = nx * ny;
        if control.angle_degrees != state.solid_angle_degrees {
            state.previous_solid = state.solid.clone();
            state.solid = Self::solid_at(&state.geometry, state.domain, control);
            state.solid_angle_degrees = control.angle_degrees;
        }
        let target = [
            state.scenario.freestream()[0] * state.lattice_speed / state.reference_speed,
            state.scenario.freestream()[1] * state.lattice_speed / state.reference_speed,
        ];
        for cell in 0..count {
            if state.previous_solid.values()[cell] != 0 && state.solid.values()[cell] == 0 {
                for direction in 0..9 {
                    let index = direction * count + cell;
                    state.populations[index] =
                        T::from_f64(Self::equilibrium(direction, 1.0, target));
                }
            }
        }
        state
            .previous_solid
            .values_mut()
            .copy_from_slice(state.solid.values());
        Self::refresh_macroscopic(state);
        for cell in 0..count {
            if state.solid.values()[cell] != 0 {
                continue;
            }
            for direction in 0..9 {
                let opposite = OPPOSITE[direction];
                let index = direction * count + cell;
                let opposite_index = opposite * count + cell;
                let f = state.populations[index].to_f64();
                let fo = state.populations[opposite_index].to_f64();
                let equilibrium =
                    Self::equilibrium(direction, state.density[cell], state.lattice_velocity[cell]);
                let opposite_equilibrium =
                    Self::equilibrium(opposite, state.density[cell], state.lattice_velocity[cell]);
                let symmetric = 0.5 * (f + fo);
                let antisymmetric = 0.5 * (f - fo);
                state.scratch[index] = T::from_f64(
                    f - state.omega_plus * (symmetric - 0.5 * (equilibrium + opposite_equilibrium))
                        - state.omega_minus
                            * (antisymmetric - 0.5 * (equilibrium - opposite_equilibrium)),
                );
            }
        }
        let channel_walls = Self::is_poiseuille(&state.scenario);
        for y in 0..ny {
            for x in 0..nx {
                let cell = y * nx + x;
                if state.solid.values()[cell] != 0 {
                    continue;
                }
                for direction in 0..9 {
                    let mut tx = x as isize + CX[direction];
                    let mut ty = y as isize + CY[direction];
                    if state.domain.periodic_x {
                        tx = tx.rem_euclid(nx as isize);
                    }
                    if state.domain.periodic_y {
                        ty = ty.rem_euclid(ny as isize);
                    }
                    let value = state.scratch[direction * count + cell];
                    if channel_walls && (ty < 0 || ty >= ny as isize) {
                        state.populations[OPPOSITE[direction] * count + cell] = value;
                        continue;
                    }
                    if tx < 0 || tx >= nx as isize || ty < 0 || ty >= ny as isize {
                        continue;
                    }
                    let target_cell = ty as usize * nx + tx as usize;
                    if state.solid.values()[target_cell] == 0 {
                        state.populations[direction * count + target_cell] = value;
                        continue;
                    }
                    let source_point = state.domain.cell_center(x, y);
                    let target_point = state.domain.cell_center(tx as usize, ty as usize);
                    let source_distance = state
                        .geometry
                        .signed_distance(source_point, control.angle_degrees)
                        .max(1.0e-8);
                    let target_distance = state
                        .geometry
                        .signed_distance(target_point, control.angle_degrees);
                    let fraction = (source_distance
                        / (source_distance - target_distance).max(1.0e-8))
                    .clamp(0.05, 0.95);
                    let upstream_x = x as isize - CX[direction];
                    let upstream_y = y as isize - CY[direction];
                    let opposite = OPPOSITE[direction];
                    let mut reflected = if fraction < 0.5
                        && upstream_x >= 0
                        && upstream_x < nx as isize
                        && upstream_y >= 0
                        && upstream_y < ny as isize
                    {
                        let upstream_cell = upstream_y as usize * nx + upstream_x as usize;
                        2.0 * fraction * value.to_f64()
                            + (1.0 - 2.0 * fraction)
                                * state.scratch[direction * count + upstream_cell].to_f64()
                    } else {
                        value.to_f64() / (2.0 * fraction)
                            + (2.0 * fraction - 1.0) / (2.0 * fraction)
                                * state.scratch[opposite * count + cell].to_f64()
                    };
                    let wall_point = [
                        source_point[0] + fraction * (target_point[0] - source_point[0]),
                        source_point[1] + fraction * (target_point[1] - source_point[1]),
                    ];
                    let wall = Self::wall_lattice_velocity(state, wall_point, control);
                    reflected -= 6.0
                        * WEIGHT[direction]
                        * state.density[cell]
                        * (CX[direction] as f64 * wall[0] + CY[direction] as f64 * wall[1]);
                    state.populations[opposite * count + cell] = T::from_f64(reflected);
                }
            }
        }
        Self::apply_boundaries(state);
    }

    fn validate_post_step(state: &mut LbmState<T>) -> Result<(f64, f64, f64), SolverError> {
        let mut minimum_population = f64::INFINITY;
        for value in &state.populations {
            let selected = value.to_f64();
            if !selected.is_finite() {
                return Err(SolverError::new(
                    FailureReason::InvalidPopulation,
                    FailureStage::Postcondition,
                    "LBM populations became non-finite",
                ));
            }
            minimum_population = minimum_population.min(selected);
        }
        if minimum_population < MINIMUM_POPULATION {
            return Err(SolverError::new(
                FailureReason::InvalidPopulation,
                FailureStage::Postcondition,
                "LBM population left the admissible envelope",
            ));
        }
        Self::refresh_macroscopic(state);
        let mut density_excursion = 0.0_f64;
        for (cell, value) in state.density.iter().copied().enumerate() {
            if state.solid.values()[cell] != 0 {
                continue;
            }
            if !value.is_finite() || value <= 0.0 {
                return Err(SolverError::new(
                    FailureReason::InvalidDensity,
                    FailureStage::Postcondition,
                    "LBM density became non-positive or non-finite",
                ));
            }
            density_excursion = density_excursion.max((value - 1.0).abs());
        }
        if density_excursion > 0.75 {
            return Err(SolverError::new(
                FailureReason::InvalidDensity,
                FailureStage::Postcondition,
                "LBM density excursion exceeded 0.75",
            ));
        }
        let scale = state.reference_speed / state.lattice_speed;
        let maximum_speed = state
            .lattice_velocity
            .iter()
            .enumerate()
            .filter(|(cell, _)| state.solid.values()[*cell] == 0)
            .map(|(_, value)| value[0].hypot(value[1]) * scale)
            .fold(0.0, f64::max);
        Ok((maximum_speed, density_excursion, minimum_population))
    }

    fn advance_candidate(
        state: &mut LbmState<T>,
        control: ControlState,
        target_dt: f64,
        minimum_substeps: usize,
        stability_retries: usize,
    ) -> Result<StepReport, SolverError> {
        let radius = state.geometry.maximum_radius();
        let current_scale = state.reference_speed / state.lattice_speed;
        let current_maximum = state
            .lattice_velocity
            .iter()
            .enumerate()
            .filter(|(cell, _)| state.solid.values()[*cell] == 0)
            .map(|(_, value)| value[0].hypot(value[1]) * current_scale)
            .fold(state.reference_speed, f64::max);
        let wall_speed = control.angular_velocity_degrees.to_radians().abs() * radius;
        let sweep_speed = (control.angle_degrees - state.control.angle_degrees)
            .to_radians()
            .abs()
            * radius
            / target_dt;
        let planning_speed = current_maximum.max(wall_speed).max(sweep_speed) * 1.25;
        let substeps =
            Self::configure_temporal_scaling(state, target_dt, planning_speed, minimum_substeps)?;
        let start = state.control;
        for step in 0..substeps {
            let fraction = (step + 1) as f64 / substeps as f64;
            Self::lattice_step(
                state,
                ControlState {
                    time: start.time + fraction * target_dt,
                    angle_degrees: start.angle_degrees
                        + fraction * (control.angle_degrees - start.angle_degrees),
                    angular_velocity_degrees: control.angular_velocity_degrees,
                },
            );
        }
        let (maximum_speed, density_excursion, minimum_population) =
            Self::validate_post_step(state)?;
        let maximum_physical = maximum_speed
            .max(state.reference_speed)
            .max(wall_speed)
            .max(sweep_speed);
        let maximum_mach =
            maximum_physical * state.lattice_speed / (state.reference_speed * LATTICE_SOUND_SPEED);
        if maximum_mach > MAXIMUM_MACH * (1.0 + 1.0e-6) {
            return Err(SolverError::new(
                FailureReason::ExcessiveVelocity,
                FailureStage::Postcondition,
                format!("post-step LBM Mach {maximum_mach} exceeds {MAXIMUM_MACH}"),
            ));
        }
        let tau_plus = 1.0 / state.omega_plus;
        let tau_minus = 1.0 / state.omega_minus;
        state.control = control;
        state.control.time = start.time + target_dt;
        state.revision = state.revision.saturating_add(1);
        state.last_substeps = substeps;
        state.stability_retries = stability_retries;
        let evidence = BTreeMap::from([
            (
                "stability_retries".into(),
                EvidenceValue::Number(stability_retries as f64),
            ),
            (
                "maximum_fluid_speed".into(),
                EvidenceValue::Number(maximum_speed),
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
                "maximum_lattice_mach".into(),
                EvidenceValue::Number(maximum_mach),
            ),
            (
                "density_excursion".into(),
                EvidenceValue::Number(density_excursion),
            ),
            (
                "minimum_population".into(),
                EvidenceValue::Number(minimum_population),
            ),
            ("omega_plus".into(), EvidenceValue::Number(state.omega_plus)),
            (
                "omega_minus".into(),
                EvidenceValue::Number(state.omega_minus),
            ),
            (
                "trt_magic".into(),
                EvidenceValue::Number((tau_plus - 0.5) * (tau_minus - 0.5)),
            ),
            (
                "requested_reynolds".into(),
                EvidenceValue::Number(state.reynolds),
            ),
            (
                "effective_reynolds".into(),
                EvidenceValue::Number(state.effective_reynolds),
            ),
        ]);
        let warnings = (state.effective_reynolds + 1.0e-9 < state.reynolds)
            .then(|| {
                format!(
                    "effective Reynolds clamped to {:.1}",
                    state.effective_reynolds
                )
            })
            .into_iter()
            .collect();
        Ok(StepReport {
            requested_dt: target_dt,
            advanced_dt: target_dt,
            substeps,
            max_speed: maximum_speed,
            state_revision: state.revision,
            evidence,
            warnings,
        })
    }

    fn make_state(
        scenario: &Scenario,
        geometry: &NacaFoil,
        start: RestartState,
    ) -> Result<LbmState<T>, SolverError> {
        if scenario.dimension() != 2 || scenario.precision() != T::PRECISION {
            return Err(SolverError::new(
                FailureReason::IncompatibleDomain,
                FailureStage::Initialization,
                "D2Q9 LBM requires matching 2D scenario precision",
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
                "LBM restart state is invalid",
            ));
        }
        let domain = GridDomain2::from_scenario(scenario).map_err(|detail| {
            SolverError::new(
                FailureReason::IncompatibleDomain,
                FailureStage::Initialization,
                detail,
            )
        })?;
        let reference_speed = Self::reference_speed(scenario);
        let control = ControlState {
            time: start.time,
            angle_degrees: start.angle_degrees,
            angular_velocity_degrees: 0.0,
        };
        let solid = Self::solid_at(geometry, domain, control);
        let count = domain.nx() * domain.ny();
        let mut state = LbmState {
            scenario: scenario.clone(),
            geometry: geometry.clone(),
            domain,
            populations: Vec::new(),
            scratch: vec![T::from_f64(0.0); 9 * count],
            density: vec![1.0; count],
            lattice_velocity: vec![[0.0; 2]; count],
            outlet: vec![T::from_f64(0.0); 9 * domain.ny()],
            sponge: Self::sponge(domain, Self::is_poiseuille(scenario)),
            previous_solid: solid.clone(),
            solid,
            solid_angle_degrees: control.angle_degrees,
            control,
            reynolds: start.reynolds,
            effective_reynolds: start.reynolds,
            reference_speed,
            lattice_speed: 1.0,
            omega_plus: 1.0,
            omega_minus: 1.0,
            revision: 0,
            last_substeps: 0,
            stability_retries: 0,
        };
        let reference_substeps = (scenario.output_dt() * reference_speed
            / (MAXIMUM_LATTICE_SPEED * domain.dx())
            - 1.0e-12)
            .ceil()
            .max(1.0) as usize;
        state.lattice_speed =
            reference_speed * scenario.output_dt() / (reference_substeps as f64 * domain.dx());
        Self::configure_relaxation(&mut state)?;
        let initial = Self::initial_velocity(scenario, domain);
        state.populations = vec![T::from_f64(0.0); 9 * count];
        for cell in 0..count {
            let physical = initial.values()[cell];
            let lattice = [
                physical[0].to_f64() * state.lattice_speed / reference_speed,
                physical[1].to_f64() * state.lattice_speed / reference_speed,
            ];
            for direction in 0..9 {
                state.populations[direction * count + cell] =
                    T::from_f64(Self::equilibrium(direction, 1.0, lattice));
            }
        }
        Self::refresh_macroscopic(&mut state);
        Self::capture_outlet(&mut state);
        Ok(state)
    }
}

impl<T: FlowScalar> FlowSolver<T> for LbmD2q9<T> {
    fn info(&self) -> &SolverInfo {
        &self.info
    }

    fn initialize(
        &mut self,
        scenario: &Scenario,
        geometry: &NacaFoil,
        _seed: u32,
    ) -> Result<(), SolverError> {
        self.state = Some(Self::make_state(
            scenario,
            geometry,
            RestartState {
                time: 0.0,
                angle_degrees: scenario.controls()[0].angle_degrees,
                reynolds: scenario.reynolds(),
            },
        )?);
        Ok(())
    }

    fn restart(
        &mut self,
        scenario: &Scenario,
        geometry: &NacaFoil,
        _seed: u32,
        start: RestartState,
    ) -> Result<(), SolverError> {
        self.state = Some(Self::make_state(scenario, geometry, start)?);
        Ok(())
    }

    fn set_reynolds(&mut self, reynolds: f64) -> Result<ReynoldsOutcome, SolverError> {
        if !reynolds.is_finite() || reynolds <= 0.0 {
            return Err(SolverError::new(
                FailureReason::InvalidRelaxation,
                FailureStage::Collision,
                "Reynolds number must be finite and positive",
            ));
        }
        let current = self.state()?;
        let mut candidate = current.clone();
        let changed = candidate.reynolds != reynolds;
        candidate.reynolds = reynolds;
        Self::configure_relaxation(&mut candidate)?;
        if changed {
            candidate.revision = candidate.revision.saturating_add(1);
        }
        let outcome = ReynoldsOutcome {
            requested: reynolds,
            effective: candidate.effective_reynolds,
            warnings: (candidate.effective_reynolds + 1.0e-9 < reynolds)
                .then(|| {
                    format!(
                        "effective Reynolds clamped to {:.1}",
                        candidate.effective_reynolds
                    )
                })
                .into_iter()
                .collect(),
        };
        self.state = Some(candidate);
        Ok(outcome)
    }

    fn advance(
        &mut self,
        control: ControlState,
        target_dt: f64,
    ) -> Result<StepReport, SolverError> {
        let current = self.state()?;
        let tolerance = if T::PRECISION == Precision::Float32 {
            1.0e-6
        } else {
            1.0e-12
        };
        if !target_dt.is_finite()
            || target_dt <= 0.0
            || !control.time.is_finite()
            || !control.angle_degrees.is_finite()
            || !control.angular_velocity_degrees.is_finite()
            || (control.time - (current.control.time + target_dt)).abs()
                > tolerance * control.time.abs().max(1.0)
        {
            return Err(SolverError::new(
                FailureReason::TimeContractFailure,
                FailureStage::TimeMapping,
                "control completion time must equal current time plus target_dt",
            ));
        }
        let mut minimum_substeps = 1;
        for retry in 0..4 {
            let mut candidate = current.clone();
            match Self::advance_candidate(
                &mut candidate,
                control,
                target_dt,
                minimum_substeps,
                retry,
            ) {
                Ok(report) => {
                    self.state = Some(candidate);
                    return Ok(report);
                }
                Err(error)
                    if retry < 3
                        && error.reason == FailureReason::ExcessiveVelocity
                        && error.stage == FailureStage::Postcondition =>
                {
                    let configured_substeps = (target_dt * candidate.reference_speed
                        / (candidate.lattice_speed * candidate.domain.dx()))
                    .round()
                    .max(1.0) as usize;
                    minimum_substeps = configured_substeps.saturating_mul(2).max(2);
                }
                Err(error) => return Err(error),
            }
        }
        unreachable!("bounded LBM retry loop returns on every branch")
    }

    fn sample_velocity(
        &self,
        points_xy: &[[T; 2]],
        output_xy: &mut [[T; 2]],
    ) -> Result<(), SolverError> {
        if points_xy.len() != output_xy.len() {
            return Err(SolverError::new(
                FailureReason::TransferFailure,
                FailureStage::Postcondition,
                "point and output lengths differ",
            ));
        }
        let state = self.state()?;
        let cells = Self::physical_velocity(state);
        for (point, output) in points_xy.iter().zip(output_xy) {
            if !point[0].is_finite() || !point[1].is_finite() {
                return Err(SolverError::new(
                    FailureReason::NonfiniteState,
                    FailureStage::Postcondition,
                    "sample point is non-finite",
                ));
            }
            *output = sample_cells(&cells, state.domain, [point[0].to_f64(), point[1].to_f64()]);
        }
        Ok(())
    }

    fn export_state(&self) -> Result<CanonicalFlowState2<T>, SolverError> {
        let state = self.state()?;
        let velocity = Self::physical_velocity(state).values().to_vec();
        let density = state
            .density
            .iter()
            .copied()
            .enumerate()
            .map(|(cell, value)| {
                T::from_f64(if state.solid.values()[cell] == 0 {
                    value
                } else {
                    1.0
                })
            })
            .collect();
        Ok(CanonicalFlowState2 {
            bounds: state.domain.bounds,
            resolution: state.domain.resolution,
            periodic_axes: state.scenario.periodic_axes().to_vec(),
            time: state.control.time,
            angle_degrees: state.control.angle_degrees,
            angular_velocity_degrees: state.control.angular_velocity_degrees,
            geometry: Self::geometry_descriptor(&state.geometry),
            producer: Producer {
                implementation: "rust".into(),
                execution_target: self.execution_target.clone(),
                build: None,
            },
            source_solver: "lbm-d2q9".into(),
            velocity,
            density: Some(density),
        })
    }

    fn import_state(
        &mut self,
        imported: &CanonicalFlowState2<T>,
        control: ControlState,
    ) -> ImportOutcome {
        let rejected = |reason, detail: &str| ImportOutcome {
            accepted: false,
            reason: Some(reason),
            stage: FailureStage::CanonicalImport,
            evidence: Evidence::new(),
            discarded_state: Vec::new(),
            warnings: vec![detail.into()],
        };
        if imported.validate_payload().is_err() {
            return rejected(
                FailureReason::NonfiniteState,
                "canonical payload is malformed",
            );
        }
        let Ok(current) = self.state() else {
            return rejected(
                FailureReason::UnsupportedConversion,
                "solver is not initialized",
            );
        };
        if imported.resolution != current.domain.resolution
            || imported.bounds != current.domain.bounds
            || imported.periodic_axes != current.scenario.periodic_axes()
        {
            return rejected(
                FailureReason::IncompatibleDomain,
                "canonical domain differs",
            );
        }
        if imported.geometry != Self::geometry_descriptor(&current.geometry) {
            return rejected(
                FailureReason::IncompatibleGeometry,
                "canonical geometry differs",
            );
        }
        if (imported.time - control.time).abs() > 1.0e-8
            || (imported.angle_degrees - control.angle_degrees).abs() > 1.0e-8
            || (imported.angular_velocity_degrees - control.angular_velocity_degrees).abs() > 1.0e-8
        {
            return rejected(
                FailureReason::TimeContractFailure,
                "canonical pose/time differs from authoritative control",
            );
        }
        let mut candidate = current.clone();
        candidate.solid = Self::solid_at(&candidate.geometry, candidate.domain, control);
        candidate.solid_angle_degrees = control.angle_degrees;
        let mut maximum_speed = 0.0_f64;
        for (cell, velocity) in imported.velocity.iter().enumerate() {
            if candidate.solid.values()[cell] == 0 {
                maximum_speed = maximum_speed.max(velocity[0].to_f64().hypot(velocity[1].to_f64()));
                if let Some(density) = &imported.density {
                    let value = density[cell].to_f64();
                    if value <= 0.0 || (value - 1.0).abs() > 0.75 {
                        return rejected(
                            FailureReason::InvalidDensity,
                            "canonical fluid density is outside the admissible envelope",
                        );
                    }
                }
            }
        }
        let wall_speed = control.angular_velocity_degrees.to_radians().abs()
            * candidate.geometry.maximum_radius();
        if let Err(error) =
            Self::configure_import_scaling(&mut candidate, maximum_speed.max(wall_speed))
        {
            return rejected(error.reason, &error.detail);
        }
        let maximum_mach = maximum_speed.max(wall_speed).max(candidate.reference_speed)
            * candidate.lattice_speed
            / (candidate.reference_speed * LATTICE_SOUND_SPEED);
        if maximum_mach > MAXIMUM_MACH * (1.0 + 1.0e-6) {
            return rejected(
                FailureReason::ExcessiveVelocity,
                "canonical import exceeds Mach limit",
            );
        }
        let count = candidate.domain.nx() * candidate.domain.ny();
        candidate.populations.fill(T::from_f64(0.0));
        for cell in 0..count {
            let density = if candidate.solid.values()[cell] == 0 {
                imported
                    .density
                    .as_ref()
                    .map_or(1.0, |values| values[cell].to_f64())
            } else {
                1.0
            };
            let physical = imported.velocity[cell];
            let lattice = [
                physical[0].to_f64() * candidate.lattice_speed / candidate.reference_speed,
                physical[1].to_f64() * candidate.lattice_speed / candidate.reference_speed,
            ];
            for direction in 0..9 {
                candidate.populations[direction * count + cell] =
                    T::from_f64(Self::equilibrium(direction, density, lattice));
            }
        }
        candidate.previous_solid = candidate.solid.clone();
        candidate.control = control;
        candidate.revision = candidate.revision.saturating_add(1);
        Self::capture_outlet(&mut candidate);
        if let Err(error) = Self::validate_post_step(&mut candidate) {
            return rejected(error.reason, &error.detail);
        }
        self.state = Some(candidate);
        ImportOutcome {
            accepted: true,
            reason: None,
            stage: FailureStage::CanonicalImport,
            evidence: Evidence::new(),
            discarded_state: vec!["non_equilibrium_populations".into()],
            warnings: vec![
                "LBM resumes from local equilibrium; an initialization transient is expected"
                    .into(),
            ],
        }
    }

    fn diagnostics(&self) -> Result<Diagnostics, SolverError> {
        let state = self.state()?;
        let cells = Self::physical_velocity(state);
        let mac = cells_to_faces(&cells, state.domain.periodic_x, state.domain.periodic_y);
        let geometry = rasterize_geometry::<T>(&state.geometry, state.domain, state.control);
        let metrics = compute_flow_metrics(
            &mac,
            state.domain,
            &geometry.solid,
            &geometry.wall_velocity,
            [
                state.scenario.freestream()[0],
                state.scenario.freestream()[1],
            ],
            state.geometry.descriptor().pivot[0],
            state.geometry.descriptor().chord,
        );
        let density_drift = state
            .density
            .iter()
            .enumerate()
            .filter(|(cell, _)| state.solid.values()[*cell] == 0)
            .map(|(_, value)| (value - 1.0).abs())
            .fold(0.0, f64::max);
        let values = BTreeMap::from([
            ("kinetic_energy".into(), metrics.kinetic_energy),
            ("enstrophy".into(), metrics.enstrophy),
            ("divergence_linf".into(), metrics.divergence_linf),
            ("solid_leakage".into(), 0.0),
            ("maximum_speed".into(), metrics.maximum_speed),
            ("maximum_wall_speed".into(), metrics.maximum_wall_speed),
            ("wake_width".into(), metrics.wake_width),
            ("recirculation_area".into(), metrics.recirculation_area),
            ("density_drift_linf".into(), density_drift),
            ("effective_reynolds".into(), state.effective_reynolds),
        ]);
        Ok(Diagnostics {
            state_revision: state.revision,
            values,
            evidence: Evidence::new(),
            warnings: (state.effective_reynolds + 1.0e-9 < state.reynolds)
                .then(|| {
                    format!(
                        "effective Reynolds clamped to {:.1}",
                        state.effective_reynolds
                    )
                })
                .into_iter()
                .collect(),
        })
    }

    fn state_revision(&self) -> u64 {
        self.state.as_ref().map_or(0, |state| state.revision)
    }
}

#[cfg(test)]
mod tests {
    use super::{LbmD2q9, convective_outlet_population, lbm_sponge_strength};
    use crate::{NacaFoil, Scenario, StableFluids, solver::FlowSolver};

    fn scenario(path: &str) -> Scenario {
        let document = match path {
            "uniform" => include_str!("../../../../../scenarios/validation/uniform.json"),
            "poiseuille" => include_str!("../../../../../scenarios/validation/poiseuille.json"),
            _ => include_str!("../../../../../scenarios/airfoil/default.json"),
        };
        Scenario::from_json(document).unwrap()
    }

    fn with_resolution(document: &str, resolution: [usize; 2]) -> Scenario {
        let mut value: serde_json::Value = serde_json::from_str(document).unwrap();
        value["resolution"] = serde_json::json!(resolution);
        Scenario::from_json(&serde_json::to_string(&value).unwrap()).unwrap()
    }

    fn run_to<T: crate::solver::FlowScalar>(
        solver: &mut LbmD2q9<T>,
        scenario: &Scenario,
        duration: f64,
    ) {
        let mut time = 0.0;
        while time + 1.0e-12 < duration {
            let dt = scenario.output_dt().min(duration - time);
            time += dt;
            solver.advance(scenario.control_at(time), dt).unwrap();
        }
    }

    #[test]
    fn revision5_boundary_formulas_are_exact() {
        assert!((convective_outlet_population(0.2, 0.5, 0.1) - 0.23).abs() < 1.0e-12);
        assert!((lbm_sponge_strength(64, 32, 10, 0, false, false, false) - 0.12).abs() < 1.0e-12);
        assert_eq!(lbm_sponge_strength(64, 32, 10, 0, false, false, true), 0.0);
        assert!((lbm_sponge_strength(64, 32, 63, 15, false, false, false) - 0.08).abs() < 1.0e-12);
    }

    #[test]
    fn advances_exact_time_with_valid_trt_evidence() {
        let scenario = scenario("uniform");
        let foil = NacaFoil::new(scenario.foil().clone()).unwrap();
        let mut solver = LbmD2q9::<f64>::default();
        solver.initialize(&scenario, &foil, 0).unwrap();
        let report = solver
            .advance(
                scenario.control_at(scenario.output_dt()),
                scenario.output_dt(),
            )
            .unwrap();
        assert_eq!(report.advanced_dt, scenario.output_dt());
        assert!(
            report
                .evidence
                .values()
                .all(crate::solver::EvidenceValue::is_valid)
        );
        let crate::solver::EvidenceValue::Number(magic) = report.evidence["trt_magic"] else {
            panic!("TRT magic evidence must be numeric");
        };
        assert!((magic - 3.0 / 16.0).abs() < 1.0e-10);
    }

    #[test]
    fn rejected_extreme_motion_rolls_back() {
        let scenario = scenario("default");
        let foil = NacaFoil::new(scenario.foil().clone()).unwrap();
        let mut solver = LbmD2q9::<f32>::default();
        solver.initialize(&scenario, &foil, 0).unwrap();
        let before = solver.export_state().unwrap();
        let control = crate::ControlState {
            time: scenario.output_dt(),
            angle_degrees: 30.0,
            angular_velocity_degrees: 1.0e12,
        };
        assert!(solver.advance(control, scenario.output_dt()).is_err());
        assert_eq!(solver.export_state().unwrap(), before);
    }

    #[test]
    fn canonical_import_reconstructs_equilibrium() {
        let scenario = scenario("uniform");
        let foil = NacaFoil::new(scenario.foil().clone()).unwrap();
        let mut solver = LbmD2q9::<f64>::default();
        solver.initialize(&scenario, &foil, 0).unwrap();
        let state = solver.export_state().unwrap();
        let outcome = solver.import_state(&state, scenario.control_at(0.0));
        assert!(outcome.accepted);
        assert_eq!(outcome.discarded_state, ["non_equilibrium_populations"]);
    }

    #[test]
    fn revision5_uniform_and_taylor_green_are_bounded() {
        let uniform = with_resolution(
            include_str!("../../../../../scenarios/validation/uniform.json"),
            [40, 20],
        );
        let foil = NacaFoil::new(uniform.foil().clone()).unwrap();
        let mut uniform_solver = LbmD2q9::<f64>::default();
        uniform_solver.initialize(&uniform, &foil, 0).unwrap();
        let initial = uniform_solver.export_state().unwrap();
        run_to(&mut uniform_solver, &uniform, 0.2);
        let final_state = uniform_solver.export_state().unwrap();
        let velocity_drift = final_state
            .velocity
            .iter()
            .zip(initial.velocity)
            .map(|(after, before)| (after[0] - before[0]).powi(2) + (after[1] - before[1]).powi(2))
            .sum::<f64>()
            / final_state.velocity.len() as f64;
        assert!(velocity_drift.sqrt() < 1.0e-5);
        assert!(uniform_solver.diagnostics().unwrap().values["density_drift_linf"] < 1.0e-5);

        let taylor = with_resolution(
            include_str!("../../../../../scenarios/validation/taylor-green.json"),
            [48, 48],
        );
        let foil = NacaFoil::new(taylor.foil().clone()).unwrap();
        let mut taylor_solver = LbmD2q9::<f64>::default();
        taylor_solver.initialize(&taylor, &foil, 0).unwrap();
        run_to(&mut taylor_solver, &taylor, 0.2);
        let state = taylor_solver.export_state().unwrap();
        let dx = (state.bounds[0][1] - state.bounds[0][0]) / state.resolution[0] as f64;
        let dy = (state.bounds[1][1] - state.bounds[1][0]) / state.resolution[1] as f64;
        let amplitude = (-2.0 * 0.2 / taylor.reynolds()).exp();
        let error = state
            .velocity
            .iter()
            .enumerate()
            .map(|(index, value)| {
                let x = index % state.resolution[0];
                let y = index / state.resolution[0];
                let px = state.bounds[0][0] + (x as f64 + 0.5) * dx;
                let py = state.bounds[1][0] + (y as f64 + 0.5) * dy;
                let expected = [
                    amplitude * px.sin() * py.cos(),
                    -amplitude * px.cos() * py.sin(),
                ];
                (value[0] - expected[0]).powi(2) + (value[1] - expected[1]).powi(2)
            })
            .sum::<f64>()
            / state.velocity.len() as f64;
        assert!(error.sqrt() < 0.08, "Taylor-Green L2 was {}", error.sqrt());
    }

    #[test]
    fn revision5_poiseuille_and_airfoil_cases_are_finite() {
        let poiseuille = with_resolution(
            include_str!("../../../../../scenarios/validation/poiseuille.json"),
            [64, 32],
        );
        let foil = NacaFoil::new(poiseuille.foil().clone()).unwrap();
        let mut channel = LbmD2q9::<f64>::default();
        channel.initialize(&poiseuille, &foil, 0).unwrap();
        run_to(&mut channel, &poiseuille, 0.2);
        let state = channel.export_state().unwrap();
        let dy = (state.bounds[1][1] - state.bounds[1][0]) / state.resolution[1] as f64;
        let error = state
            .velocity
            .iter()
            .enumerate()
            .map(|(index, value)| {
                let y = index / state.resolution[0];
                let py = state.bounds[1][0] + (y as f64 + 0.5) * dy;
                let expected = 1.5 * (1.0 - py.powi(2));
                (value[0] - expected).powi(2) + value[1].powi(2)
            })
            .sum::<f64>()
            / state.velocity.len() as f64;
        assert!(error.sqrt() < 0.25, "Poiseuille L2 was {}", error.sqrt());

        let dynamic = with_resolution(
            include_str!("../../../../../scenarios/airfoil/default.json"),
            [32, 20],
        );
        let foil = NacaFoil::new(dynamic.foil().clone()).unwrap();
        let mut airfoil = LbmD2q9::<f32>::default();
        airfoil.initialize(&dynamic, &foil, 0).unwrap();
        run_to(&mut airfoil, &dynamic, 0.05);
        assert!(
            airfoil
                .diagnostics()
                .unwrap()
                .values
                .values()
                .all(|value| value.is_finite())
        );
    }

    #[test]
    fn full_preview_accepts_static_complete_stall() {
        let scenario = scenario("default");
        let foil = NacaFoil::new(scenario.foil().clone()).unwrap();
        let mut solver = LbmD2q9::<f32>::default();
        solver.initialize(&scenario, &foil, 0).unwrap();
        let dt = scenario.output_dt();
        let report = solver
            .advance(
                crate::ControlState {
                    time: dt,
                    angle_degrees: 30.0,
                    angular_velocity_degrees: 0.0,
                },
                dt,
            )
            .unwrap();
        assert_eq!(report.advanced_dt, dt);
        assert!(
            report
                .evidence
                .values()
                .all(crate::solver::EvidenceValue::is_valid)
        );
    }

    #[test]
    fn stable_and_lbm_import_each_other_transactionally() {
        let scenario = scenario("default");
        let foil = NacaFoil::new(scenario.foil().clone()).unwrap();
        let mut stable = StableFluids::<f32>::default();
        let mut lbm = LbmD2q9::<f32>::default();
        stable.initialize(&scenario, &foil, 0).unwrap();
        lbm.initialize(&scenario, &foil, 0).unwrap();
        let control = scenario.control_at(0.0);
        assert!(
            lbm.import_state(&stable.export_state().unwrap(), control)
                .accepted
        );
        assert!(
            stable
                .import_state(&lbm.export_state().unwrap(), control)
                .accepted
        );
        let dt = scenario.output_dt();
        lbm.advance(scenario.control_at(dt), dt).unwrap();
        stable.advance(scenario.control_at(dt), dt).unwrap();
    }
}
