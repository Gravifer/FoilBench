use std::{collections::BTreeMap, error::Error, fmt};

use serde::{Deserialize, Serialize};

use crate::scenario::{ControlState, Precision, Scenario};

#[derive(Clone, Debug, PartialEq, Serialize, Deserialize)]
pub struct SolverInfo {
    pub id: String,
    pub display_name: String,
    pub dimensions: Vec<u8>,
    pub supports_moving_boundary: bool,
    pub supported_precisions: Vec<Precision>,
    pub acceleration: String,
}

#[derive(Clone, Debug, PartialEq)]
pub struct StepReport {
    pub requested_dt: f64,
    pub advanced_dt: f64,
    pub substeps: usize,
    pub max_speed: f64,
    pub state_revision: u64,
    pub evidence: BTreeMap<String, f64>,
    pub warnings: Vec<String>,
}

#[derive(Clone, Debug, PartialEq, Eq)]
pub struct SolverError {
    pub reason: &'static str,
    pub stage: &'static str,
    pub detail: String,
}

impl fmt::Display for SolverError {
    fn fmt(&self, formatter: &mut fmt::Formatter<'_>) -> fmt::Result {
        write!(
            formatter,
            "{} at {}: {}",
            self.reason, self.stage, self.detail
        )
    }
}

impl Error for SolverError {}

pub trait FlowSolver {
    fn info(&self) -> &SolverInfo;
    /// Initialize from a validated scenario and deterministic seed.
    ///
    /// # Errors
    ///
    /// Returns a classified solver error when initialization is inadmissible.
    fn initialize(&mut self, scenario: &Scenario, seed: u64) -> Result<(), SolverError>;
    /// Advance exactly the requested physical interval transactionally.
    ///
    /// # Errors
    ///
    /// Returns a classified solver error without committing a failed step.
    fn advance(&mut self, control: ControlState, target_dt: f64)
    -> Result<StepReport, SolverError>;
    /// Sample a batch of points without transferring solver ownership.
    ///
    /// # Errors
    ///
    /// Returns a classified error for invalid shapes or non-finite sampling state.
    fn sample_velocity(
        &self,
        points_xy: &[[f64; 2]],
        output_xy: &mut [[f64; 2]],
    ) -> Result<(), SolverError>;
    fn state_revision(&self) -> u64;
}
