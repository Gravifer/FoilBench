//! Platform-neutral `FoilBench` contracts and deterministic foundations.

pub mod canonical;
pub mod field;
pub mod geometry;
pub mod grid;
pub mod implementation;
pub mod lbm;
pub mod metrics;
pub mod pcg32;
pub mod pic_flip;
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
pub use lbm::{LbmD2q9, convective_outlet_population, lbm_sponge_strength};
pub use metrics::{FlowMetrics, compute_flow_metrics};
pub use pcg32::Pcg32;
pub use pic_flip::PicFlip;
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

#[cfg(test)]
mod conformance_tests {
    use serde_json::Value;

    use super::{
        ControlState, EvidenceValue, FlowSolver, LbmD2q9, NacaFoil, PicFlip, RestartState,
        Scenario, StableFluids,
    };

    fn fixture_number(value: &Value, key: &str) -> f64 {
        value[key]
            .as_f64()
            .unwrap_or_else(|| panic!("missing numeric {key}"))
    }

    fn retry_scenario(case: &Value) -> Scenario {
        let mut document: Value = serde_json::from_str(include_str!(
            "../../../../../scenarios/airfoil/default.json"
        ))
        .unwrap();
        if let Some(resolution) = case.get("resolution") {
            document["resolution"] = resolution.clone();
        }
        if let Some(target_dt) = case.get("target_dt") {
            document["output_dt"] = target_dt.clone();
        }
        if let Some(options) = case.get("solver_options").and_then(Value::as_object) {
            for (key, value) in options {
                document["solver_options"][key] = value.clone();
            }
        }
        Scenario::from_json(&serde_json::to_string(&document).unwrap()).unwrap()
    }

    #[test]
    fn planning_retry_fixture_executes_every_declared_step() {
        let fixture: Value = serde_json::from_str(include_str!(
            "../../../../../spec/conformance/solver-validity.json"
        ))
        .unwrap();
        let cases = fixture["planning_retry_cases"].as_object().unwrap();

        for solver_id in ["stable-fluids", "pic-flip", "lbm-d2q9"] {
            let case = &cases[solver_id];
            let scenario = retry_scenario(case);
            let geometry = NacaFoil::new(scenario.foil().clone()).unwrap();
            let expected_steps = case["expected_steps"]
                .as_u64()
                .and_then(|value| u32::try_from(value).ok())
                .unwrap();
            assert!(expected_steps >= 1);
            let angle = fixture_number(case, "angle_degrees");
            let angular_velocity = case["angular_velocity_degrees"].as_f64().unwrap_or(0.0);
            let target_dt = case["target_dt"]
                .as_f64()
                .unwrap_or_else(|| scenario.output_dt());
            let mut solver: Box<dyn FlowSolver<f32>> = match solver_id {
                "stable-fluids" => Box::new(StableFluids::<f32>::default()),
                "pic-flip" => Box::new(PicFlip::<f32>::default()),
                "lbm-d2q9" => Box::new(LbmD2q9::<f32>::default()),
                _ => unreachable!(),
            };
            if solver_id == "lbm-d2q9" {
                solver
                    .restart(
                        &scenario,
                        &geometry,
                        scenario.seed(),
                        RestartState {
                            time: 0.0,
                            angle_degrees: angle,
                            reynolds: scenario.reynolds(),
                        },
                    )
                    .unwrap();
            } else {
                solver
                    .initialize(&scenario, &geometry, scenario.seed())
                    .unwrap();
            }

            let mut total_retries = 0.0;
            for step in 1..=expected_steps {
                let report = solver
                    .advance(
                        ControlState {
                            time: f64::from(step) * target_dt,
                            angle_degrees: angle,
                            angular_velocity_degrees: angular_velocity,
                        },
                        target_dt,
                    )
                    .unwrap();
                assert_eq!(report.state_revision, u64::from(step));
                let EvidenceValue::Number(retries) = report.evidence["stability_retries"] else {
                    panic!("stability_retries must be numeric");
                };
                total_retries += retries;
            }
            assert_eq!(solver.state_revision(), u64::from(expected_steps));
            assert!(total_retries >= fixture_number(case, "minimum_total_stability_retries"));
        }
    }
}
