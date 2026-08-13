//! Platform-neutral `FoilBench` contracts and deterministic foundations.

pub mod canonical;
pub mod geometry;
pub mod pcg32;
pub mod scenario;
pub mod solver;

pub use canonical::{CanonicalManifest, CanonicalManifestV1, CanonicalManifestV2};
pub use geometry::{FoilDescriptor, NacaFoil};
pub use pcg32::Pcg32;
pub use scenario::{ControlState, Scenario};
pub use solver::{FlowSolver, SolverError, SolverInfo};
