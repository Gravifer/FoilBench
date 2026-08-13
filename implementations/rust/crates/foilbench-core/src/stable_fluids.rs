//! Native/WASM shared Stable Fluids implementation.

#![allow(
    clippy::cast_possible_truncation,
    clippy::cast_possible_wrap,
    clippy::cast_precision_loss,
    clippy::cast_sign_loss,
    clippy::float_cmp,
    clippy::missing_panics_doc,
    clippy::too_many_lines
)]

use std::collections::BTreeMap;

use crate::{
    canonical::{CanonicalGeometryDescriptor, Producer},
    field::{MacGrid2, VectorField2},
    geometry::NacaFoil,
    grid::{
        GridDomain2, apply_domain_boundaries, cells_to_faces, enforce_solid_faces, faces_to_cells,
        rk2_backtrace, sample_cells,
    },
    metrics::{FlowMetrics, compute_flow_metrics},
    projection::{ProjectionReport, project_incompressible},
    raster::{GeometryFields2, rasterize_geometry},
    scenario::{ControlState, Scenario},
    solver::{
        CanonicalFlowState2, Diagnostics, Evidence, EvidenceValue, FailureReason, FailureStage,
        FlowScalar, FlowSolver, ImportOutcome, InteractiveTuning, RestartState, ReynoldsOutcome,
        SolverError, SolverInfo, StepReport, TuningValue,
    },
    viscosity::{DiffusionReport, diffuse_mac},
};

const MAX_SUBSTEPS: usize = 512;

#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum StableTransport {
    SemiLagrangian,
    MacCormack,
    SkewRk2,
}

impl StableTransport {
    fn parse(value: &str) -> Result<Self, SolverError> {
        match value {
            "semi-lagrangian" => Ok(Self::SemiLagrangian),
            "maccormack" => Ok(Self::MacCormack),
            "skew-rk2" => Ok(Self::SkewRk2),
            _ => Err(SolverError::new(
                FailureReason::UnsupportedConversion,
                FailureStage::Initialization,
                "unsupported Stable Fluids transport mode",
            )),
        }
    }

    const fn id(self) -> &'static str {
        match self {
            Self::SemiLagrangian => "semi-lagrangian",
            Self::MacCormack => "maccormack",
            Self::SkewRk2 => "skew-rk2",
        }
    }
}

#[derive(Clone)]
struct StableState<T: FlowScalar> {
    scenario: Scenario,
    geometry: NacaFoil,
    domain: GridDomain2,
    velocity: MacGrid2<T>,
    geometry_fields: GeometryFields2<T>,
    control: ControlState,
    reynolds: f64,
    transport: StableTransport,
    revision: u64,
    last_projection: ProjectionReport,
    last_diffusion: DiffusionReport,
    last_substeps: usize,
    stability_retries: usize,
}

#[derive(Clone)]
pub(crate) struct ParticleGridSnapshot<T: FlowScalar> {
    pub velocity: MacGrid2<T>,
    pub solid: crate::field::ScalarField2<u8>,
    pub control: ControlState,
}

#[derive(Clone)]
pub struct StableFluids<T: FlowScalar> {
    info: SolverInfo,
    execution_target: String,
    state: Option<StableState<T>>,
}

impl<T: FlowScalar> Default for StableFluids<T> {
    fn default() -> Self {
        Self::new("native")
    }
}

impl<T: FlowScalar> StableFluids<T> {
    #[must_use]
    pub fn new(execution_target: impl Into<String>) -> Self {
        Self {
            info: SolverInfo {
                id: "stable-fluids".into(),
                display_name: "Stable Fluids (MAC)".into(),
                dimensions: vec![2],
                supports_moving_boundary: true,
                supported_precisions: vec![
                    crate::scenario::Precision::Float32,
                    crate::scenario::Precision::Float64,
                ],
                acceleration: "Rust matrix-free deterministic CPU".into(),
            },
            execution_target: execution_target.into(),
            state: None,
        }
    }

    #[must_use]
    pub fn current_reynolds(&self) -> Option<f64> {
        self.state.as_ref().map(|state| state.reynolds)
    }

    pub(crate) fn particle_grid_snapshot(&self) -> Result<ParticleGridSnapshot<T>, SolverError> {
        let state = self.state()?;
        Ok(ParticleGridSnapshot {
            velocity: state.velocity.clone(),
            solid: state.geometry_fields.solid.clone(),
            control: state.control,
        })
    }

    pub(crate) fn stage_particle_faces(
        &mut self,
        velocity: MacGrid2<T>,
    ) -> Result<(), SolverError> {
        let state = self.state.as_mut().ok_or_else(|| {
            SolverError::new(
                FailureReason::UnsupportedConversion,
                FailureStage::Initialization,
                "Stable Fluids has not been initialized",
            )
        })?;
        if (velocity.nx(), velocity.ny()) != (state.domain.nx(), state.domain.ny())
            || !velocity.is_finite()
        {
            return Err(SolverError::new(
                FailureReason::TransferFailure,
                FailureStage::ParticleTransfer,
                "particle transfer produced an invalid MAC grid",
            ));
        }
        state.velocity = velocity;
        Ok(())
    }

    pub(crate) fn advance_particle_grid(
        &mut self,
        control: ControlState,
        dt: f64,
    ) -> Result<(ProjectionReport, DiffusionReport), SolverError> {
        let state = self.state.as_mut().ok_or_else(|| {
            SolverError::new(
                FailureReason::UnsupportedConversion,
                FailureStage::Initialization,
                "Stable Fluids has not been initialized",
            )
        })?;
        if !dt.is_finite() || dt <= 0.0 {
            return Err(SolverError::new(
                FailureReason::TimeContractFailure,
                FailureStage::TimeMapping,
                "particle-grid interval must be finite and positive",
            ));
        }
        state.geometry_fields = rasterize_geometry(&state.geometry, state.domain, control);
        let freestream = [
            state.scenario.freestream()[0],
            state.scenario.freestream()[1],
        ];
        let poiseuille = Self::is_poiseuille(&state.scenario);
        apply_domain_boundaries(&mut state.velocity, state.domain, freestream, poiseuille);
        enforce_solid_faces(
            &mut state.velocity,
            &state.geometry_fields.solid,
            &state.geometry_fields.wall_velocity,
        );
        state.last_diffusion = diffuse_mac(
            &mut state.velocity,
            state.domain,
            &state.geometry_fields.solid,
            &state.geometry_fields.wall_velocity,
            1.0 / state.reynolds,
            dt,
            Self::option_f64(&state.scenario, "viscosity_tolerance", 1.0e-5)?,
            Self::option_usize(&state.scenario, "viscosity_max_iterations", 640)?,
            freestream,
            poiseuille,
        )?;
        state.last_projection = project_incompressible(
            &mut state.velocity,
            state.domain,
            &state.geometry_fields.solid,
            dt,
            Self::option_f64(&state.scenario, "pressure_tolerance", 1.0e-5)?,
            Self::option_usize(&state.scenario, "pressure_max_iterations", 640)?,
            freestream,
            poiseuille,
        )?;
        enforce_solid_faces(
            &mut state.velocity,
            &state.geometry_fields.solid,
            &state.geometry_fields.wall_velocity,
        );
        if !state.velocity.is_finite() {
            return Err(SolverError::new(
                FailureReason::NonfiniteState,
                FailureStage::Postcondition,
                "particle-grid projection produced non-finite velocity",
            ));
        }
        state.control = control;
        Ok((state.last_projection, state.last_diffusion))
    }

    /// Select a transport mode without advancing physical time.
    ///
    /// # Errors
    ///
    /// Returns an error for an unsupported mode or uninitialized solver.
    pub fn set_transport(&mut self, mode: &str) -> Result<(), SolverError> {
        let selected = StableTransport::parse(mode)?;
        let state = self.state.as_mut().ok_or_else(|| {
            SolverError::new(
                FailureReason::UnsupportedConversion,
                FailureStage::Initialization,
                "solver is not initialized",
            )
        })?;
        if selected != state.transport {
            state.transport = selected;
            state.revision = state.revision.saturating_add(1);
        }
        Ok(())
    }

    fn state(&self) -> Result<&StableState<T>, SolverError> {
        self.state.as_ref().ok_or_else(|| {
            SolverError::new(
                FailureReason::UnsupportedConversion,
                FailureStage::Initialization,
                "Stable Fluids has not been initialized",
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
        let selected = scenario
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
            })?;
        Ok(selected)
    }

    fn postcondition_limit(scenario: &Scenario, name: &str) -> Result<f64, SolverError> {
        let Some(value) = scenario.solver_options().get(name) else {
            return Ok(f64::INFINITY);
        };
        let selected = value.as_f64().ok_or_else(|| {
            SolverError::new(
                FailureReason::PostconditionFailure,
                FailureStage::Postcondition,
                format!("postcondition option {name} must be numeric"),
            )
        })?;
        if !selected.is_finite() || selected < 0.0 {
            return Err(SolverError::new(
                FailureReason::PostconditionFailure,
                FailureStage::Postcondition,
                format!("postcondition option {name} must be finite and non-negative"),
            ));
        }
        Ok(selected)
    }

    fn transport(scenario: &Scenario) -> Result<StableTransport, SolverError> {
        StableTransport::parse(
            scenario
                .solver_options()
                .get("stable_advection")
                .and_then(serde_json::Value::as_str)
                .unwrap_or("maccormack"),
        )
    }

    fn is_poiseuille(scenario: &Scenario) -> bool {
        scenario
            .solver_options()
            .get("initial_condition")
            .and_then(serde_json::Value::as_str)
            == Some("poiseuille")
    }

    fn initial_cells(scenario: &Scenario, domain: GridDomain2) -> VectorField2<T> {
        let freestream = [
            T::from_f64(scenario.freestream()[0]),
            T::from_f64(scenario.freestream()[1]),
        ];
        let mut field = VectorField2::filled(domain.nx(), domain.ny(), freestream);
        let initial = scenario
            .solver_options()
            .get("initial_condition")
            .and_then(serde_json::Value::as_str)
            .unwrap_or("freestream");
        for y in 0..domain.ny() {
            for x in 0..domain.nx() {
                let point = domain.cell_center(x, y);
                match initial {
                    "taylor-green" => field.set(
                        x,
                        y,
                        [
                            T::from_f64(point[0].sin() * point[1].cos()),
                            T::from_f64(-point[0].cos() * point[1].sin()),
                        ],
                    ),
                    "poiseuille" => {
                        let center = 0.5 * (domain.bounds[1][0] + domain.bounds[1][1]);
                        let radius = 0.5 * (domain.bounds[1][1] - domain.bounds[1][0]);
                        let profile = 1.5 * (1.0 - ((point[1] - center) / radius).powi(2));
                        field.set(x, y, [T::from_f64(profile), T::from_f64(0.0)]);
                    }
                    _ => {}
                }
            }
        }
        field
    }

    fn build_state(
        scenario: &Scenario,
        geometry: &NacaFoil,
        start: RestartState,
    ) -> Result<StableState<T>, SolverError> {
        if scenario.dimension() != 2 || scenario.precision() != T::PRECISION {
            return Err(SolverError::new(
                FailureReason::IncompatibleDomain,
                FailureStage::Initialization,
                "Stable Fluids requires a matching 2D precision scenario",
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
                "restart state is non-finite or non-positive",
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
        let geometry_fields = rasterize_geometry(geometry, domain, control);
        let mut velocity = cells_to_faces(
            &Self::initial_cells(scenario, domain),
            domain.periodic_x,
            domain.periodic_y,
        );
        let freestream = [scenario.freestream()[0], scenario.freestream()[1]];
        apply_domain_boundaries(
            &mut velocity,
            domain,
            freestream,
            Self::is_poiseuille(scenario),
        );
        enforce_solid_faces(
            &mut velocity,
            &geometry_fields.solid,
            &geometry_fields.wall_velocity,
        );
        let projection = project_incompressible(
            &mut velocity,
            domain,
            &geometry_fields.solid,
            scenario.output_dt().max(1.0e-4),
            Self::option_f64(scenario, "pressure_tolerance", 1.0e-5)?,
            Self::option_usize(scenario, "pressure_max_iterations", 640)?,
            freestream,
            Self::is_poiseuille(scenario),
        )?;
        enforce_solid_faces(
            &mut velocity,
            &geometry_fields.solid,
            &geometry_fields.wall_velocity,
        );
        Ok(StableState {
            scenario: scenario.clone(),
            geometry: geometry.clone(),
            domain,
            velocity,
            geometry_fields,
            control,
            reynolds: start.reynolds,
            transport: Self::transport(scenario)?,
            revision: 0,
            last_projection: projection,
            last_diffusion: DiffusionReport {
                converged: true,
                iterations: 0,
                residual_linf: 0.0,
            },
            last_substeps: 0,
            stability_retries: 0,
        })
    }

    fn velocity_scale(cells: &VectorField2<T>) -> ([f64; 2], f64) {
        let mut components = [0.0_f64; 2];
        let mut maximum = 0.0_f64;
        for velocity in cells.values() {
            let x = velocity[0].to_f64();
            let y = velocity[1].to_f64();
            components[0] = components[0].max(x.abs());
            components[1] = components[1].max(y.abs());
            maximum = maximum.max(x.hypot(y));
        }
        (components, maximum)
    }

    fn planned_substeps(
        state: &StableState<T>,
        target: ControlState,
        target_dt: f64,
    ) -> Result<(usize, usize), SolverError> {
        let cells = faces_to_cells(&state.velocity);
        let (components, _) = Self::velocity_scale(&cells);
        let fluid_rate = components[0] / state.domain.dx() + components[1] / state.domain.dy();
        let wall_speed =
            target.angular_velocity_degrees.to_radians().abs() * state.geometry.maximum_radius();
        let cfl = Self::option_f64(
            &state.scenario,
            "stable_cfl",
            if state.transport == StableTransport::SkewRk2 {
                0.4
            } else {
                1.25
            },
        )?;
        if cfl <= 0.0 {
            return Err(SolverError::new(
                FailureReason::StabilityLimit,
                FailureStage::TimeMapping,
                "stable_cfl must be positive",
            ));
        }
        let advective = (fluid_rate * target_dt / cfl).ceil().max(1.0) as usize;
        let boundary = (wall_speed * target_dt / (0.7 * state.domain.dx().min(state.domain.dy())))
            .ceil()
            .max(1.0) as usize;
        let selected = advective.max(boundary);
        if selected > MAX_SUBSTEPS {
            return Err(SolverError::new(
                FailureReason::StabilityLimit,
                FailureStage::TimeMapping,
                "requested interval exceeds the Stable Fluids substep ceiling",
            ));
        }
        Ok((selected, selected.saturating_sub(1)))
    }

    fn semi_lagrangian(cells: &VectorField2<T>, domain: GridDomain2, dt: f64) -> VectorField2<T> {
        let mut output = cells.clone();
        for y in 0..domain.ny() {
            for x in 0..domain.nx() {
                let departure = rk2_backtrace(cells, domain, domain.cell_center(x, y), dt);
                output.set(x, y, sample_cells(cells, domain, departure));
            }
        }
        output
    }

    fn local_bounds(
        cells: &VectorField2<T>,
        domain: GridDomain2,
        point: [f64; 2],
    ) -> ([f64; 2], [f64; 2]) {
        let gx = ((point[0] - domain.bounds[0][0]) / domain.dx() - 0.5)
            .clamp(0.0, (domain.nx() - 1) as f64);
        let gy = ((point[1] - domain.bounds[1][0]) / domain.dy() - 0.5)
            .clamp(0.0, (domain.ny() - 1) as f64);
        let x0 = gx.floor() as usize;
        let y0 = gy.floor() as usize;
        let x1 = (x0 + 1).min(domain.nx() - 1);
        let y1 = (y0 + 1).min(domain.ny() - 1);
        let mut lower = [f64::INFINITY; 2];
        let mut upper = [f64::NEG_INFINITY; 2];
        for (x, y) in [(x0, y0), (x1, y0), (x0, y1), (x1, y1)] {
            let value = cells.get(x, y);
            for component in 0..2 {
                lower[component] = lower[component].min(value[component].to_f64());
                upper[component] = upper[component].max(value[component].to_f64());
            }
        }
        (lower, upper)
    }

    fn maccormack(cells: &VectorField2<T>, domain: GridDomain2, dt: f64) -> VectorField2<T> {
        let forward = Self::semi_lagrangian(cells, domain, dt);
        let backward = Self::semi_lagrangian(&forward, domain, -dt);
        let mut output = forward.clone();
        for y in 0..domain.ny() {
            for x in 0..domain.nx() {
                let departure = rk2_backtrace(cells, domain, domain.cell_center(x, y), dt);
                let (lower, upper) = Self::local_bounds(cells, domain, departure);
                let original = cells.get(x, y);
                let selected = forward.get(x, y);
                let reverse = backward.get(x, y);
                output.set(
                    x,
                    y,
                    [
                        T::from_f64(
                            (selected[0].to_f64()
                                + 0.5 * (original[0].to_f64() - reverse[0].to_f64()))
                            .clamp(lower[0], upper[0]),
                        ),
                        T::from_f64(
                            (selected[1].to_f64()
                                + 0.5 * (original[1].to_f64() - reverse[1].to_f64()))
                            .clamp(lower[1], upper[1]),
                        ),
                    ],
                );
            }
        }
        output
    }

    fn sampled(cells: &VectorField2<T>, domain: GridDomain2, x: isize, y: isize) -> [f64; 2] {
        let selected_x = if domain.periodic_x {
            x.rem_euclid(domain.nx() as isize) as usize
        } else {
            x.clamp(0, domain.nx() as isize - 1) as usize
        };
        let selected_y = if domain.periodic_y {
            y.rem_euclid(domain.ny() as isize) as usize
        } else {
            y.clamp(0, domain.ny() as isize - 1) as usize
        };
        let value = cells.get(selected_x, selected_y);
        [value[0].to_f64(), value[1].to_f64()]
    }

    fn skew_rate(cells: &VectorField2<T>, domain: GridDomain2) -> VectorField2<T> {
        let mut rate = cells.clone();
        for y in 0..domain.ny() {
            for x in 0..domain.nx() {
                let center = Self::sampled(cells, domain, x as isize, y as isize);
                let left = Self::sampled(cells, domain, x as isize - 1, y as isize);
                let right = Self::sampled(cells, domain, x as isize + 1, y as isize);
                let bottom = Self::sampled(cells, domain, x as isize, y as isize - 1);
                let top = Self::sampled(cells, domain, x as isize, y as isize + 1);
                let mut selected = [T::from_f64(0.0); 2];
                for component in 0..2 {
                    let gradient_x = (right[component] - left[component]) / (2.0 * domain.dx());
                    let gradient_y = (top[component] - bottom[component]) / (2.0 * domain.dy());
                    let flux_x = (right[0] * right[component] - left[0] * left[component])
                        / (2.0 * domain.dx());
                    let flux_y = (top[1] * top[component] - bottom[1] * bottom[component])
                        / (2.0 * domain.dy());
                    selected[component] = T::from_f64(
                        -0.5 * (center[0] * gradient_x + center[1] * gradient_y + flux_x + flux_y),
                    );
                }
                rate.set(x, y, selected);
            }
        }
        rate
    }

    fn skew_rk2(cells: &VectorField2<T>, domain: GridDomain2, dt: f64) -> VectorField2<T> {
        let first = Self::skew_rate(cells, domain);
        let midpoint = VectorField2::from_vec(
            domain.nx(),
            domain.ny(),
            cells
                .values()
                .iter()
                .zip(first.values())
                .map(|(value, rate)| {
                    [
                        T::from_f64(value[0].to_f64() + 0.5 * dt * rate[0].to_f64()),
                        T::from_f64(value[1].to_f64() + 0.5 * dt * rate[1].to_f64()),
                    ]
                })
                .collect(),
        )
        .expect("matching velocity and rate shapes");
        let second = Self::skew_rate(&midpoint, domain);
        VectorField2::from_vec(
            domain.nx(),
            domain.ny(),
            cells
                .values()
                .iter()
                .zip(second.values())
                .map(|(value, rate)| {
                    [
                        T::from_f64(value[0].to_f64() + dt * rate[0].to_f64()),
                        T::from_f64(value[1].to_f64() + dt * rate[1].to_f64()),
                    ]
                })
                .collect(),
        )
        .expect("matching velocity and rate shapes")
    }

    fn advance_candidate(
        candidate: &mut StableState<T>,
        target: ControlState,
        target_dt: f64,
    ) -> Result<StepReport, SolverError> {
        let (substeps, stability_retries) = Self::planned_substeps(candidate, target, target_dt)?;
        let dt = target_dt / substeps as f64;
        let start = candidate.control;
        for substep in 0..substeps {
            let fraction = (substep + 1) as f64 / substeps as f64;
            let sub_control = ControlState {
                time: start.time + fraction * target_dt,
                angle_degrees: start.angle_degrees
                    + fraction * (target.angle_degrees - start.angle_degrees),
                angular_velocity_degrees: target.angular_velocity_degrees,
            };
            let cells = faces_to_cells(&candidate.velocity);
            let advected = match candidate.transport {
                StableTransport::SemiLagrangian => {
                    Self::semi_lagrangian(&cells, candidate.domain, dt)
                }
                StableTransport::MacCormack => Self::maccormack(&cells, candidate.domain, dt),
                StableTransport::SkewRk2 => Self::skew_rk2(&cells, candidate.domain, dt),
            };
            candidate.velocity = cells_to_faces(
                &advected,
                candidate.domain.periodic_x,
                candidate.domain.periodic_y,
            );
            candidate.geometry_fields =
                rasterize_geometry(&candidate.geometry, candidate.domain, sub_control);
            let freestream = [
                candidate.scenario.freestream()[0],
                candidate.scenario.freestream()[1],
            ];
            let poiseuille = Self::is_poiseuille(&candidate.scenario);
            apply_domain_boundaries(
                &mut candidate.velocity,
                candidate.domain,
                freestream,
                poiseuille,
            );
            enforce_solid_faces(
                &mut candidate.velocity,
                &candidate.geometry_fields.solid,
                &candidate.geometry_fields.wall_velocity,
            );
            candidate.last_diffusion = diffuse_mac(
                &mut candidate.velocity,
                candidate.domain,
                &candidate.geometry_fields.solid,
                &candidate.geometry_fields.wall_velocity,
                1.0 / candidate.reynolds,
                dt,
                Self::option_f64(&candidate.scenario, "viscosity_tolerance", 1.0e-5)?,
                Self::option_usize(&candidate.scenario, "viscosity_max_iterations", 640)?,
                freestream,
                poiseuille,
            )?;
            candidate.last_projection = project_incompressible(
                &mut candidate.velocity,
                candidate.domain,
                &candidate.geometry_fields.solid,
                dt,
                Self::option_f64(&candidate.scenario, "pressure_tolerance", 1.0e-5)?,
                Self::option_usize(&candidate.scenario, "pressure_max_iterations", 640)?,
                freestream,
                poiseuille,
            )?;
            enforce_solid_faces(
                &mut candidate.velocity,
                &candidate.geometry_fields.solid,
                &candidate.geometry_fields.wall_velocity,
            );
            if !candidate.velocity.is_finite() {
                return Err(SolverError::new(
                    FailureReason::NonfiniteState,
                    FailureStage::Postcondition,
                    "Stable Fluids produced non-finite velocity",
                ));
            }
            candidate.control = sub_control;
        }
        candidate.control = target;
        candidate.revision = candidate.revision.saturating_add(1);
        candidate.last_substeps = substeps;
        candidate.stability_retries = stability_retries;
        let metrics = Self::metrics(candidate);
        let (components, maximum) = Self::velocity_scale(&faces_to_cells(&candidate.velocity));
        let characteristic = (components[0] * dt / candidate.domain.dx())
            .max(components[1] * dt / candidate.domain.dy());
        let rate = components[0] / candidate.domain.dx() + components[1] / candidate.domain.dy();
        let boundary_sweep =
            metrics.maximum_wall_speed * dt / candidate.domain.dx().min(candidate.domain.dy());
        if metrics.divergence_linf
            > Self::postcondition_limit(&candidate.scenario, "mac_maximum_divergence_linf")?
            || metrics.solid_leakage
                > Self::postcondition_limit(&candidate.scenario, "mac_maximum_solid_leakage")?
        {
            return Err(SolverError::new(
                FailureReason::PostconditionFailure,
                FailureStage::Postcondition,
                "MAC divergence or solid leakage exceeds the configured limit",
            ));
        }
        let mut evidence = Evidence::new();
        for (key, value) in [
            ("maximum_fluid_speed", maximum),
            ("maximum_advective_rate", rate),
            ("maximum_characteristic_displacement", characteristic),
            ("maximum_boundary_sweep", boundary_sweep),
            ("stability_retries", stability_retries as f64),
            (
                "pressure_iterations",
                candidate.last_projection.iterations as f64,
            ),
            (
                "pressure_relative_residual",
                candidate.last_projection.residual_linf,
            ),
            (
                "viscosity_iterations",
                candidate.last_diffusion.iterations as f64,
            ),
            (
                "viscosity_final_residual",
                candidate.last_diffusion.residual_linf,
            ),
            ("divergence_linf", metrics.divergence_linf),
            ("solid_leakage", metrics.solid_leakage),
            ("requested_reynolds", candidate.reynolds),
            ("effective_reynolds", candidate.reynolds),
        ] {
            evidence.insert(key.into(), EvidenceValue::Number(value));
        }
        evidence.insert(
            "pressure_converged".into(),
            EvidenceValue::Boolean(candidate.last_projection.converged),
        );
        evidence.insert(
            "viscosity_converged".into(),
            EvidenceValue::Boolean(candidate.last_diffusion.converged),
        );
        Ok(StepReport {
            requested_dt: target_dt,
            advanced_dt: target_dt,
            substeps,
            max_speed: maximum,
            state_revision: candidate.revision,
            evidence,
            warnings: Vec::new(),
        })
    }

    fn metrics(state: &StableState<T>) -> FlowMetrics {
        compute_flow_metrics(
            &state.velocity,
            state.domain,
            &state.geometry_fields.solid,
            &state.geometry_fields.wall_velocity,
            [
                state.scenario.freestream()[0],
                state.scenario.freestream()[1],
            ],
            state.geometry.descriptor().pivot[0],
            state.geometry.descriptor().chord,
        )
    }

    fn geometry_descriptor(geometry: &NacaFoil) -> CanonicalGeometryDescriptor {
        let selected = geometry.descriptor();
        CanonicalGeometryDescriptor {
            family: selected.family.clone(),
            naca: selected.naca.clone(),
            chord: selected.chord,
            pivot: selected.pivot.clone(),
        }
    }
}

impl<T: FlowScalar> FlowSolver<T> for StableFluids<T> {
    fn info(&self) -> &SolverInfo {
        &self.info
    }

    fn initialize(
        &mut self,
        scenario: &Scenario,
        geometry: &NacaFoil,
        _seed: u32,
    ) -> Result<(), SolverError> {
        self.state = Some(Self::build_state(
            scenario,
            geometry,
            RestartState {
                time: 0.0,
                angle_degrees: scenario.control_at(0.0).angle_degrees,
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
        self.state = Some(Self::build_state(scenario, geometry, start)?);
        Ok(())
    }

    fn set_reynolds(&mut self, reynolds: f64) -> Result<ReynoldsOutcome, SolverError> {
        if !reynolds.is_finite() || reynolds <= 0.0 {
            return Err(SolverError::new(
                FailureReason::TimeContractFailure,
                FailureStage::TimeMapping,
                "Reynolds number must be finite and positive",
            ));
        }
        let state = self.state.as_mut().ok_or_else(|| {
            SolverError::new(
                FailureReason::UnsupportedConversion,
                FailureStage::Initialization,
                "Stable Fluids has not been initialized",
            )
        })?;
        if state.reynolds != reynolds {
            state.reynolds = reynolds;
            state.revision = state.revision.saturating_add(1);
        }
        Ok(ReynoldsOutcome {
            requested: reynolds,
            effective: reynolds,
            warnings: Vec::new(),
        })
    }

    fn advance(
        &mut self,
        control: ControlState,
        target_dt: f64,
    ) -> Result<StepReport, SolverError> {
        let current = self.state()?;
        if !target_dt.is_finite()
            || target_dt <= 0.0
            || !control.time.is_finite()
            || !control.angle_degrees.is_finite()
            || !control.angular_velocity_degrees.is_finite()
            || (control.time - (current.control.time + target_dt)).abs() > 1.0e-8
        {
            return Err(SolverError::new(
                FailureReason::TimeContractFailure,
                FailureStage::TimeMapping,
                "control completion time must equal current time plus target_dt",
            ));
        }
        let mut candidate = current.clone();
        let report = Self::advance_candidate(&mut candidate, control, target_dt)?;
        self.state = Some(candidate);
        Ok(report)
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
        let cells = faces_to_cells(&state.velocity);
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
        let mut velocity = faces_to_cells(&state.velocity).values().to_vec();
        for (value, is_solid) in velocity
            .iter_mut()
            .zip(state.geometry_fields.solid.values())
        {
            if *is_solid != 0 {
                *value = [T::from_f64(0.0), T::from_f64(0.0)];
            }
        }
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
            source_solver: "stable-fluids".into(),
            velocity,
            density: None,
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
                "canonical domain differs from destination",
            );
        }
        if imported.geometry != Self::geometry_descriptor(&current.geometry) {
            return rejected(
                FailureReason::IncompatibleGeometry,
                "canonical geometry differs from destination",
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
        let cells = VectorField2::from_vec(
            candidate.domain.nx(),
            candidate.domain.ny(),
            imported.velocity.clone(),
        )
        .expect("canonical payload shape was validated");
        candidate.control = control;
        candidate.geometry_fields =
            rasterize_geometry(&candidate.geometry, candidate.domain, control);
        candidate.velocity = cells_to_faces(
            &cells,
            candidate.domain.periodic_x,
            candidate.domain.periodic_y,
        );
        let freestream = [
            candidate.scenario.freestream()[0],
            candidate.scenario.freestream()[1],
        ];
        let poiseuille = Self::is_poiseuille(&candidate.scenario);
        apply_domain_boundaries(
            &mut candidate.velocity,
            candidate.domain,
            freestream,
            poiseuille,
        );
        enforce_solid_faces(
            &mut candidate.velocity,
            &candidate.geometry_fields.solid,
            &candidate.geometry_fields.wall_velocity,
        );
        match project_incompressible(
            &mut candidate.velocity,
            candidate.domain,
            &candidate.geometry_fields.solid,
            0.01,
            Self::option_f64(&candidate.scenario, "pressure_tolerance", 1.0e-5).unwrap_or(1.0e-5),
            Self::option_usize(&candidate.scenario, "pressure_max_iterations", 640).unwrap_or(640),
            freestream,
            poiseuille,
        ) {
            Ok(report) => candidate.last_projection = report,
            Err(error) => return rejected(error.reason, &error.detail),
        }
        enforce_solid_faces(
            &mut candidate.velocity,
            &candidate.geometry_fields.solid,
            &candidate.geometry_fields.wall_velocity,
        );
        candidate.revision = candidate.revision.saturating_add(1);
        self.state = Some(candidate);
        ImportOutcome {
            accepted: true,
            reason: None,
            stage: FailureStage::CanonicalImport,
            evidence: Evidence::new(),
            discarded_state: vec!["pressure_history".into()],
            warnings: Vec::new(),
        }
    }

    fn diagnostics(&self) -> Result<Diagnostics, SolverError> {
        let state = self.state()?;
        let metrics = Self::metrics(state);
        let values = BTreeMap::from([
            ("kinetic_energy".into(), metrics.kinetic_energy),
            ("enstrophy".into(), metrics.enstrophy),
            ("divergence_linf".into(), metrics.divergence_linf),
            ("solid_leakage".into(), metrics.solid_leakage),
            ("maximum_speed".into(), metrics.maximum_speed),
            ("maximum_wall_speed".into(), metrics.maximum_wall_speed),
            ("wake_width".into(), metrics.wake_width),
            ("recirculation_area".into(), metrics.recirculation_area),
        ]);
        Ok(Diagnostics {
            state_revision: state.revision,
            values,
            evidence: Evidence::new(),
            warnings: Vec::new(),
        })
    }

    fn interactive_tuning(&self) -> Option<InteractiveTuning> {
        self.state.as_ref().map(|state| InteractiveTuning {
            id: "stable-advection".into(),
            label: "adv".into(),
            value: TuningValue::Choice(state.transport.id().into()),
            can_decrease: state.transport != StableTransport::MacCormack,
            can_increase: state.transport != StableTransport::SkewRk2,
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
                "solver is not initialized",
            )
        })?;
        let selected = if direction < 0 {
            StableTransport::MacCormack
        } else {
            StableTransport::SkewRk2
        };
        if selected != state.transport {
            state.transport = selected;
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
    use super::StableFluids;
    use crate::{geometry::NacaFoil, scenario::Scenario, solver::FlowSolver};

    fn uniform() -> Scenario {
        Scenario::from_json(include_str!(
            "../../../../../scenarios/validation/uniform.json"
        ))
        .unwrap()
    }

    fn default_airfoil() -> Scenario {
        Scenario::from_json(include_str!(
            "../../../../../scenarios/airfoil/default.json"
        ))
        .unwrap()
    }

    fn scenario_with_resolution(document: &str, resolution: [usize; 2]) -> Scenario {
        let mut value: serde_json::Value = serde_json::from_str(document).unwrap();
        value["resolution"] = serde_json::json!(resolution);
        Scenario::from_json(&serde_json::to_string(&value).unwrap()).unwrap()
    }

    fn run_to(solver: &mut impl FlowSolver<f64>, scenario: &Scenario, duration: f64) {
        let mut time = 0.0;
        while time + 1.0e-12 < duration {
            let dt = scenario.output_dt().min(duration - time);
            time += dt;
            solver.advance(scenario.control_at(time), dt).unwrap();
        }
    }

    #[test]
    fn uniform_advance_is_finite_and_exact_time() {
        let scenario = uniform();
        let foil = NacaFoil::new(scenario.foil().clone()).unwrap();
        let mut solver = StableFluids::<f64>::default();
        solver.initialize(&scenario, &foil, 0).unwrap();
        let dt = 0.02;
        let report = solver.advance(scenario.control_at(dt), dt).unwrap();
        assert_eq!(report.advanced_dt, dt);
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

    #[test]
    fn rejected_step_rolls_back_every_public_state() {
        let scenario = uniform();
        let foil = NacaFoil::new(scenario.foil().clone()).unwrap();
        let mut solver = StableFluids::<f64>::default();
        solver.initialize(&scenario, &foil, 0).unwrap();
        let before = solver.export_state().unwrap();
        assert!(
            solver
                .advance(
                    crate::scenario::ControlState {
                        time: 0.02,
                        angle_degrees: 0.0,
                        angular_velocity_degrees: 1.0e12
                    },
                    0.02
                )
                .is_err()
        );
        assert_eq!(solver.export_state().unwrap(), before);
    }

    #[test]
    fn canonical_roundtrip_is_accepted_symmetrically() {
        let scenario = uniform();
        let foil = NacaFoil::new(scenario.foil().clone()).unwrap();
        let mut solver = StableFluids::<f64>::default();
        solver.initialize(&scenario, &foil, 0).unwrap();
        let exported = solver.export_state().unwrap();
        let outcome = solver.import_state(&exported, scenario.control_at(0.0));
        assert!(outcome.accepted);
        assert_eq!(solver.state_revision(), 1);
    }

    #[test]
    fn full_preview_f32_accepts_a_large_angle_step() {
        let scenario = default_airfoil();
        let foil = NacaFoil::new(scenario.foil().clone()).unwrap();
        let mut solver = StableFluids::<f32>::default();
        solver.initialize(&scenario, &foil, 0).unwrap();
        let dt = scenario.output_dt();
        let report = solver
            .advance(
                crate::scenario::ControlState {
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
    fn revision5_taylor_green_error_is_bounded() {
        let scenario = scenario_with_resolution(
            include_str!("../../../../../scenarios/validation/taylor-green.json"),
            [48, 48],
        );
        let foil = NacaFoil::new(scenario.foil().clone()).unwrap();
        let mut solver = StableFluids::<f64>::default();
        solver.initialize(&scenario, &foil, 0).unwrap();
        run_to(&mut solver, &scenario, 0.2);
        let state = solver.export_state().unwrap();
        let dx = (state.bounds[0][1] - state.bounds[0][0]) / state.resolution[0] as f64;
        let dy = (state.bounds[1][1] - state.bounds[1][0]) / state.resolution[1] as f64;
        let amplitude = (-2.0 * 0.2 / scenario.reynolds()).exp();
        let squared_error = state
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
        assert!(squared_error.sqrt() < 0.08);
    }

    #[test]
    fn revision5_poiseuille_profile_is_retained() {
        let scenario = scenario_with_resolution(
            include_str!("../../../../../scenarios/validation/poiseuille.json"),
            [64, 32],
        );
        let foil = NacaFoil::new(scenario.foil().clone()).unwrap();
        let mut solver = StableFluids::<f64>::default();
        solver.initialize(&scenario, &foil, 0).unwrap();
        run_to(&mut solver, &scenario, 0.2);
        let state = solver.export_state().unwrap();
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
        assert!(error.sqrt() < 0.25);
    }

    #[test]
    fn revision5_naca0012_zero_angle_remains_symmetric() {
        let scenario = Scenario::from_json(include_str!(
            "../../../../../scenarios/validation/naca0012-zero.json"
        ))
        .unwrap();
        let foil = NacaFoil::new(scenario.foil().clone()).unwrap();
        let mut solver = StableFluids::<f32>::default();
        solver.initialize(&scenario, &foil, 0).unwrap();
        let mut time = 0.0;
        while time + 1.0e-12 < 0.5 {
            let dt = scenario.output_dt().min(0.5 - time);
            time += dt;
            solver.advance(scenario.control_at(time), dt).unwrap();
        }
        let state = solver.export_state().unwrap();
        let nx = state.resolution[0];
        let ny = state.resolution[1];
        let mut squared = 0.0;
        for y in 0..ny / 2 {
            for x in 0..nx {
                let lower = state.velocity[y * nx + x];
                let upper = state.velocity[(ny - 1 - y) * nx + x];
                squared +=
                    f64::from(lower[0] - upper[0]).powi(2) + f64::from(lower[1] + upper[1]).powi(2);
            }
        }
        let rms = (squared / (nx * (ny / 2)) as f64).sqrt();
        assert!(rms < 0.01, "symmetry RMS was {rms}");
        assert!(solver.diagnostics().unwrap().values["solid_leakage"] < 1.0e-6);
    }

    #[test]
    fn revision5_dynamic_naca_metrics_are_finite() {
        let scenario = scenario_with_resolution(
            include_str!("../../../../../scenarios/airfoil/default.json"),
            [32, 20],
        );
        let foil = NacaFoil::new(scenario.foil().clone()).unwrap();
        let mut solver = StableFluids::<f32>::default();
        solver.initialize(&scenario, &foil, 0).unwrap();
        let mut time = 0.0;
        while time + 1.0e-12 < 0.05 {
            let dt = scenario.output_dt().min(0.05 - time);
            time += dt;
            solver.advance(scenario.control_at(time), dt).unwrap();
        }
        assert!(
            solver
                .diagnostics()
                .unwrap()
                .values
                .values()
                .all(|value| value.is_finite())
        );
    }

    #[test]
    fn skew_rk2_accepts_a_large_angle_step() {
        let scenario = scenario_with_resolution(
            include_str!("../../../../../scenarios/airfoil/chaotic-experimental.json"),
            [64, 32],
        );
        let foil = NacaFoil::new(scenario.foil().clone()).unwrap();
        let mut solver = StableFluids::<f32>::default();
        solver.initialize(&scenario, &foil, 0).unwrap();
        let dt = scenario.output_dt();
        solver
            .advance(
                crate::scenario::ControlState {
                    time: dt,
                    angle_degrees: 30.0,
                    angular_velocity_degrees: 0.0,
                },
                dt,
            )
            .unwrap();
        assert_eq!(
            solver.interactive_tuning().unwrap().value,
            crate::solver::TuningValue::Choice("skew-rk2".into())
        );
    }
}
