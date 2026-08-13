use std::{
    collections::{BTreeMap, BTreeSet},
    error::Error,
    fmt,
};

use serde::{Deserialize, Serialize};

use crate::{
    canonical::{CanonicalGeometryDescriptor, Producer},
    geometry::NacaFoil,
    scenario::{ControlState, Precision, Scenario},
};

pub trait FlowScalar: Copy + fmt::Debug + PartialEq + Send + Sync + 'static {
    const PRECISION: Precision;
    fn is_finite(self) -> bool;
    fn from_f64(value: f64) -> Self;
    fn to_f64(self) -> f64;
}

impl FlowScalar for f32 {
    const PRECISION: Precision = Precision::Float32;
    fn is_finite(self) -> bool {
        self.is_finite()
    }
    #[allow(clippy::cast_possible_truncation)]
    fn from_f64(value: f64) -> Self {
        value as Self
    }
    fn to_f64(self) -> f64 {
        f64::from(self)
    }
}

impl FlowScalar for f64 {
    const PRECISION: Precision = Precision::Float64;
    fn is_finite(self) -> bool {
        self.is_finite()
    }
    fn from_f64(value: f64) -> Self {
        value
    }
    fn to_f64(self) -> f64 {
        self
    }
}

#[derive(Clone, Debug, PartialEq, Serialize, Deserialize)]
pub struct SolverInfo {
    pub id: String,
    pub display_name: String,
    pub dimensions: Vec<u8>,
    pub supports_moving_boundary: bool,
    pub supported_precisions: Vec<Precision>,
    pub acceleration: String,
}

#[derive(Clone, Copy, Debug, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
#[non_exhaustive]
pub enum FailureReason {
    ExcessiveVelocity,
    StabilityLimit,
    NonfiniteState,
    ConvergenceFailure,
    ProjectionFailure,
    InvalidDensity,
    InvalidPopulation,
    InvalidRelaxation,
    TransferFailure,
    PostconditionFailure,
    TimeContractFailure,
    IncompatibleGeometry,
    IncompatibleDomain,
    UnsupportedConversion,
}

#[derive(Clone, Copy, Debug, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "kebab-case")]
#[non_exhaustive]
pub enum FailureStage {
    Initialization,
    Restart,
    CanonicalImport,
    Advection,
    Viscosity,
    Projection,
    Boundary,
    Collision,
    Streaming,
    ParticleTransfer,
    ParticleAdvection,
    PopulationMaintenance,
    TimeMapping,
    Postcondition,
}

#[derive(Clone, Debug, PartialEq, Serialize, Deserialize)]
#[serde(untagged)]
pub enum EvidenceValue {
    Number(f64),
    Boolean(bool),
    Text(String),
}

impl EvidenceValue {
    #[must_use]
    pub fn is_valid(&self) -> bool {
        match self {
            Self::Number(value) => value.is_finite(),
            Self::Boolean(_) => true,
            Self::Text(value) => !value.is_empty() && value.len() <= 128,
        }
    }
}

pub type Evidence = BTreeMap<String, EvidenceValue>;

#[derive(Clone, Debug, PartialEq)]
pub struct StepReport {
    pub requested_dt: f64,
    pub advanced_dt: f64,
    pub substeps: usize,
    pub max_speed: f64,
    pub state_revision: u64,
    pub evidence: Evidence,
    pub warnings: Vec<String>,
}

#[derive(Clone, Copy, Debug, PartialEq)]
pub struct RestartState {
    pub time: f64,
    pub angle_degrees: f64,
    pub reynolds: f64,
}

#[derive(Clone, Debug, PartialEq)]
pub struct ReynoldsOutcome {
    pub requested: f64,
    pub effective: f64,
    pub warnings: Vec<String>,
}

#[derive(Clone, Debug, PartialEq)]
pub struct Diagnostics {
    pub state_revision: u64,
    pub values: BTreeMap<String, f64>,
    pub evidence: Evidence,
    pub warnings: Vec<String>,
}

#[derive(Clone, Debug, PartialEq)]
pub enum TuningValue {
    Choice(String),
    Number(f64),
}

#[derive(Clone, Debug, PartialEq)]
pub struct InteractiveTuning {
    pub id: String,
    pub label: String,
    pub value: TuningValue,
    pub can_decrease: bool,
    pub can_increase: bool,
}

#[derive(Clone, Debug, PartialEq, Serialize)]
pub struct CanonicalFlowState2<T: FlowScalar> {
    pub bounds: [[f64; 2]; 2],
    pub resolution: [usize; 2],
    pub periodic_axes: Vec<String>,
    pub time: f64,
    pub angle_degrees: f64,
    pub angular_velocity_degrees: f64,
    pub geometry: CanonicalGeometryDescriptor,
    pub producer: Producer,
    pub source_solver: String,
    pub velocity: Vec<[T; 2]>,
    pub density: Option<Vec<T>>,
}

impl<T: FlowScalar> CanonicalFlowState2<T> {
    /// Validate payload shape and finiteness before canonical import.
    ///
    /// # Errors
    ///
    /// Returns `nonfinite_state` when the payload is malformed.
    pub fn validate_payload(&self) -> Result<(), SolverError> {
        let cells = self.resolution[0].saturating_mul(self.resolution[1]);
        let velocity_valid = self.velocity.len() == cells
            && self
                .velocity
                .iter()
                .flatten()
                .copied()
                .all(FlowScalar::is_finite);
        let density_valid = self.density.as_ref().is_none_or(|density| {
            density.len() == cells && density.iter().copied().all(FlowScalar::is_finite)
        });
        let metadata_valid = self
            .bounds
            .iter()
            .all(|bound| bound[0].is_finite() && bound[1].is_finite() && bound[1] > bound[0])
            && self.time.is_finite()
            && self.time >= 0.0
            && self.angle_degrees.is_finite()
            && self.angular_velocity_degrees.is_finite()
            && self
                .periodic_axes
                .iter()
                .all(|axis| matches!(axis.as_str(), "x" | "y"))
            && self.periodic_axes.iter().collect::<BTreeSet<_>>().len() == self.periodic_axes.len()
            && self.geometry.family == "naca-four-digit-v1"
            && self.geometry.naca.len() == 4
            && self
                .geometry
                .naca
                .bytes()
                .all(|digit| digit.is_ascii_digit())
            && self.geometry.chord.is_finite()
            && self.geometry.chord > 0.0
            && self.geometry.pivot.len() == 2
            && self.geometry.pivot.iter().all(|value| value.is_finite())
            && !self.producer.implementation.is_empty()
            && !self.producer.execution_target.is_empty()
            && !self.source_solver.is_empty();
        if !metadata_valid || !velocity_valid || !density_valid {
            return Err(SolverError::new(
                FailureReason::NonfiniteState,
                FailureStage::CanonicalImport,
                "canonical payload shape or finiteness is invalid",
            ));
        }
        Ok(())
    }
}

#[derive(Clone, Debug, PartialEq)]
pub struct ImportOutcome {
    pub accepted: bool,
    pub reason: Option<FailureReason>,
    pub stage: FailureStage,
    pub evidence: Evidence,
    pub discarded_state: Vec<String>,
    pub warnings: Vec<String>,
}

#[derive(Clone, Debug, PartialEq, Eq)]
pub struct SolverError {
    pub reason: FailureReason,
    pub stage: FailureStage,
    pub detail: String,
}

impl SolverError {
    #[must_use]
    pub fn new(reason: FailureReason, stage: FailureStage, detail: impl Into<String>) -> Self {
        Self {
            reason,
            stage,
            detail: detail.into(),
        }
    }
}

impl fmt::Display for SolverError {
    fn fmt(&self, formatter: &mut fmt::Formatter<'_>) -> fmt::Result {
        write!(
            formatter,
            "{:?} at {:?}: {}",
            self.reason, self.stage, self.detail
        )
    }
}

impl Error for SolverError {}

#[allow(clippy::missing_errors_doc)]
pub trait FlowSolver<T: FlowScalar> {
    fn info(&self) -> &SolverInfo;
    fn initialize(
        &mut self,
        scenario: &Scenario,
        geometry: &NacaFoil,
        seed: u32,
    ) -> Result<(), SolverError>;
    fn restart(
        &mut self,
        scenario: &Scenario,
        geometry: &NacaFoil,
        seed: u32,
        start: RestartState,
    ) -> Result<(), SolverError>;
    fn set_reynolds(&mut self, reynolds: f64) -> Result<ReynoldsOutcome, SolverError>;
    fn advance(&mut self, control: ControlState, target_dt: f64)
    -> Result<StepReport, SolverError>;
    fn sample_velocity(
        &self,
        points_xy: &[[T; 2]],
        output_xy: &mut [[T; 2]],
    ) -> Result<(), SolverError>;
    fn export_state(&self) -> Result<CanonicalFlowState2<T>, SolverError>;
    fn import_state(
        &mut self,
        state: &CanonicalFlowState2<T>,
        control: ControlState,
    ) -> ImportOutcome;
    fn diagnostics(&self) -> Result<Diagnostics, SolverError>;
    fn interactive_tuning(&self) -> Option<InteractiveTuning> {
        None
    }
    fn adjust_interactive_tuning(
        &mut self,
        _direction: i8,
    ) -> Result<Option<InteractiveTuning>, SolverError> {
        Ok(self.interactive_tuning())
    }
    fn state_revision(&self) -> u64;
}

#[cfg(test)]
mod tests {
    use super::{EvidenceValue, FailureReason, FailureStage};

    #[test]
    fn failure_vocabulary_uses_contract_spellings() {
        assert_eq!(
            serde_json::to_string(&FailureReason::NonfiniteState).unwrap(),
            "\"nonfinite_state\""
        );
        assert_eq!(
            serde_json::to_string(&FailureStage::CanonicalImport).unwrap(),
            "\"canonical-import\""
        );
    }

    #[test]
    fn evidence_rejects_nonfinite_and_unbounded_text() {
        assert!(!EvidenceValue::Number(f64::NAN).is_valid());
        assert!(!EvidenceValue::Text(String::new()).is_valid());
        assert!(EvidenceValue::Boolean(true).is_valid());
    }
}
