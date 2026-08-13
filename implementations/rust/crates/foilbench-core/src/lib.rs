//! Platform-neutral `FoilBench` contracts and deterministic foundations.

pub mod canonical;
pub mod geometry;
pub mod implementation;
pub mod pcg32;
pub mod scenario;
pub mod solver;

pub use canonical::{
    CanonicalGeometryDescriptor, CanonicalManifest, CanonicalManifestError, CanonicalManifestV1,
    CanonicalManifestV2, Producer,
};
pub use geometry::{FoilDescriptor, NacaFoil};
pub use implementation::{ExecutionTarget, ImplementationDescription, implementation_description};
pub use pcg32::Pcg32;
pub use scenario::{ControlState, Precision, Scenario, ScenarioError};
pub use solver::{
    CanonicalFlowState2, Diagnostics, Evidence, EvidenceValue, FailureReason, FailureStage,
    FlowScalar, FlowSolver, ImportOutcome, InteractiveTuning, RestartState, ReynoldsOutcome,
    SolverError, SolverInfo, StepReport, TuningValue,
};
