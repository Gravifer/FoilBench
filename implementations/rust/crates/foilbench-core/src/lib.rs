//! Platform-neutral `FoilBench` contracts and deterministic foundations.

pub mod canonical;
pub mod field;
pub mod geometry;
pub mod grid;
pub mod implementation;
pub mod metrics;
pub mod pcg32;
pub mod projection;
pub mod raster;
pub mod scenario;
pub mod solver;
pub mod stable_fluids;
pub mod viscosity;

pub use canonical::{
    CanonicalGeometryDescriptor, CanonicalManifest, CanonicalManifestError, CanonicalManifestV1,
    CanonicalManifestV2, Producer,
};
pub use field::{MacGrid2, ScalarField2, VectorField2};
pub use geometry::{FoilDescriptor, NacaFoil};
pub use grid::{
    GridDomain2, apply_domain_boundaries, cells_to_faces, divergence, enforce_solid_faces,
    faces_to_cells, rk2_backtrace, sample_cells,
};
pub use implementation::{ExecutionTarget, ImplementationDescription, implementation_description};
pub use metrics::{FlowMetrics, compute_flow_metrics};
pub use pcg32::Pcg32;
pub use projection::{ProjectionReport, project_incompressible};
pub use raster::{GeometryFields2, rasterize_geometry};
pub use scenario::{ControlState, Precision, Scenario, ScenarioError};
pub use solver::{
    CanonicalFlowState2, Diagnostics, Evidence, EvidenceValue, FailureReason, FailureStage,
    FlowScalar, FlowSolver, ImportOutcome, InteractiveTuning, RestartState, ReynoldsOutcome,
    SolverError, SolverInfo, StepReport, TuningValue,
};
pub use stable_fluids::{StableFluids, StableTransport};
pub use viscosity::{DiffusionReport, diffuse_mac};
