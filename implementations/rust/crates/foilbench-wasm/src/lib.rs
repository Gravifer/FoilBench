//! Coarse `wasm-bindgen` boundary over the shared Rust solver core.

#![allow(
    clippy::missing_errors_doc,
    clippy::must_use_candidate,
    clippy::needless_pass_by_value
)]

use foilbench_core::{
    CanonicalFlowState2, CanonicalGeometryDescriptor, ControlState, FlowSolver, NacaFoil,
    Precision, Producer, RestartState, Scenario, SolverError, StableFluids, TuningValue,
};
use serde::Deserialize;
use serde_json::{Value, json};
use wasm_bindgen::prelude::*;

enum SolverStorage {
    Float32(StableFluids<f32>),
    Float64(StableFluids<f64>),
}

#[derive(Deserialize)]
struct CanonicalWire {
    bounds: [[f64; 2]; 2],
    resolution: [usize; 2],
    periodic_axes: Vec<String>,
    time: f64,
    angle_degrees: f64,
    angular_velocity_degrees: f64,
    geometry: CanonicalGeometryDescriptor,
    producer: Producer,
    source_solver: String,
}

fn js_error(error: &SolverError) -> JsValue {
    JsValue::from_str(
        &json!({
            "kind": "numerical_failure",
            "reason": error.reason,
            "stage": error.stage,
            "message": error.detail,
        })
        .to_string(),
    )
}

fn text_error(detail: impl Into<String>) -> JsValue {
    JsValue::from_str(&json!({"kind": "programming_error", "message": detail.into()}).to_string())
}

fn report_json(report: &foilbench_core::StepReport) -> String {
    json!({
        "requestedDt": report.requested_dt,
        "advancedDt": report.advanced_dt,
        "substeps": report.substeps,
        "maxSpeed": report.max_speed,
        "stateRevision": report.state_revision,
        "evidence": report.evidence,
        "warnings": report.warnings,
    })
    .to_string()
}

fn import_json(outcome: &foilbench_core::ImportOutcome) -> String {
    json!({
        "status": if outcome.accepted { "accepted" } else { "rejected" },
        "reason": outcome.reason.map_or_else(|| Value::String("none".into()), |reason| serde_json::to_value(reason).unwrap_or(Value::String("unsupported_conversion".into()))),
        "stage": outcome.stage,
        "evidence": outcome.evidence,
        "discardedState": outcome.discarded_state,
        "warnings": outcome.warnings,
    }).to_string()
}

/// Foundation handshake used before creating a solver.
#[wasm_bindgen]
#[must_use]
pub fn describe() -> String {
    serde_json::to_string(&foilbench_core::implementation_description(
        foilbench_core::ExecutionTarget::WasmBrowser,
    ))
    .unwrap_or_else(|_| String::from(r#"{"implementation":"rust","execution_target":"wasm-browser","phase":"description-error","solvers":[]}"#))
}

#[wasm_bindgen]
pub struct WasmSolver {
    storage: Option<SolverStorage>,
    scenario: Scenario,
    geometry: NacaFoil,
}

#[wasm_bindgen]
impl WasmSolver {
    /// Parse a shared scenario and initialize one Rust/WASM solver.
    #[wasm_bindgen(constructor)]
    pub fn new(scenario_json: &str, solver_id: &str, seed: u32) -> Result<WasmSolver, JsValue> {
        if solver_id != "stable-fluids" {
            return Err(text_error("this milestone exposes only stable-fluids"));
        }
        let scenario =
            Scenario::from_json(scenario_json).map_err(|error| text_error(error.to_string()))?;
        let geometry = NacaFoil::new(scenario.foil().clone()).map_err(text_error)?;
        let storage = match scenario.precision() {
            Precision::Float32 => {
                let mut solver = StableFluids::<f32>::new("wasm-browser");
                solver
                    .initialize(&scenario, &geometry, seed)
                    .map_err(|error| js_error(&error))?;
                SolverStorage::Float32(solver)
            }
            Precision::Float64 => {
                let mut solver = StableFluids::<f64>::new("wasm-browser");
                solver
                    .initialize(&scenario, &geometry, seed)
                    .map_err(|error| js_error(&error))?;
                SolverStorage::Float64(solver)
            }
        };
        Ok(Self {
            storage: Some(storage),
            scenario,
            geometry,
        })
    }

    #[wasm_bindgen(getter)]
    pub fn precision(&self) -> String {
        match self.storage {
            Some(SolverStorage::Float32(_)) => "float32",
            Some(SolverStorage::Float64(_)) => "float64",
            None => "disposed",
        }
        .into()
    }

    #[wasm_bindgen(getter)]
    pub fn state_revision(&self) -> u64 {
        match &self.storage {
            Some(SolverStorage::Float32(solver)) => solver.state_revision(),
            Some(SolverStorage::Float64(solver)) => solver.state_revision(),
            None => 0,
        }
    }

    #[wasm_bindgen(getter)]
    pub fn reynolds(&self) -> f64 {
        match &self.storage {
            Some(SolverStorage::Float32(solver)) => solver.current_reynolds().unwrap_or(f64::NAN),
            Some(SolverStorage::Float64(solver)) => solver.current_reynolds().unwrap_or(f64::NAN),
            None => f64::NAN,
        }
    }

    pub fn info_json(&self) -> Result<String, JsValue> {
        let info = match &self.storage {
            Some(SolverStorage::Float32(solver)) => solver.info(),
            Some(SolverStorage::Float64(solver)) => solver.info(),
            None => return Err(text_error("solver is disposed")),
        };
        serde_json::to_string(info).map_err(|error| text_error(error.to_string()))
    }

    pub fn restart(
        &mut self,
        time: f64,
        angle_degrees: f64,
        reynolds: f64,
        seed: u32,
    ) -> Result<(), JsValue> {
        let start = RestartState {
            time,
            angle_degrees,
            reynolds,
        };
        match self.storage.as_mut() {
            Some(SolverStorage::Float32(solver)) => solver
                .restart(&self.scenario, &self.geometry, seed, start)
                .map_err(|error| js_error(&error)),
            Some(SolverStorage::Float64(solver)) => solver
                .restart(&self.scenario, &self.geometry, seed, start)
                .map_err(|error| js_error(&error)),
            None => Err(text_error("solver is disposed")),
        }
    }

    pub fn advance_json(
        &mut self,
        time: f64,
        angle_degrees: f64,
        angular_velocity_degrees: f64,
        target_dt: f64,
    ) -> Result<String, JsValue> {
        let control = ControlState {
            time,
            angle_degrees,
            angular_velocity_degrees,
        };
        let report = match self.storage.as_mut() {
            Some(SolverStorage::Float32(solver)) => solver.advance(control, target_dt),
            Some(SolverStorage::Float64(solver)) => solver.advance(control, target_dt),
            None => return Err(text_error("solver is disposed")),
        }
        .map_err(|error| js_error(&error))?;
        Ok(report_json(&report))
    }

    pub fn set_reynolds_json(&mut self, reynolds: f64) -> Result<String, JsValue> {
        let outcome = match self.storage.as_mut() {
            Some(SolverStorage::Float32(solver)) => solver.set_reynolds(reynolds),
            Some(SolverStorage::Float64(solver)) => solver.set_reynolds(reynolds),
            None => return Err(text_error("solver is disposed")),
        }
        .map_err(|error| js_error(&error))?;
        Ok(json!({"requested": outcome.requested, "effective": outcome.effective, "warnings": outcome.warnings}).to_string())
    }

    pub fn tuning_json(&self) -> Result<String, JsValue> {
        let tuning = match &self.storage {
            Some(SolverStorage::Float32(solver)) => solver.interactive_tuning(),
            Some(SolverStorage::Float64(solver)) => solver.interactive_tuning(),
            None => return Err(text_error("solver is disposed")),
        };
        Ok(tuning.map_or_else(|| "null".into(), |selected| json!({"id": selected.id, "label": selected.label, "value": match selected.value { TuningValue::Choice(value) => Value::String(value), TuningValue::Number(value) => json!(value) }, "canDecrease": selected.can_decrease, "canIncrease": selected.can_increase}).to_string()))
    }

    pub fn set_transport(&mut self, mode: &str) -> Result<(), JsValue> {
        match self.storage.as_mut() {
            Some(SolverStorage::Float32(solver)) => solver.set_transport(mode),
            Some(SolverStorage::Float64(solver)) => solver.set_transport(mode),
            None => return Err(text_error("solver is disposed")),
        }
        .map_err(|error| js_error(&error))
    }

    pub fn sample_velocity_f32(&self, points_xy: &[f32]) -> Result<Vec<f32>, JsValue> {
        if points_xy.len() % 2 != 0 {
            return Err(text_error("point payload length must be even"));
        }
        let Some(SolverStorage::Float32(solver)) = &self.storage else {
            return Err(text_error("solver precision is not float32"));
        };
        let points = points_xy
            .chunks_exact(2)
            .map(|point| [point[0], point[1]])
            .collect::<Vec<_>>();
        let mut output = vec![[0.0_f32; 2]; points.len()];
        solver
            .sample_velocity(&points, &mut output)
            .map_err(|error| js_error(&error))?;
        Ok(output.into_iter().flatten().collect())
    }

    pub fn sample_velocity_f64(&self, points_xy: &[f64]) -> Result<Vec<f64>, JsValue> {
        if points_xy.len() % 2 != 0 {
            return Err(text_error("point payload length must be even"));
        }
        let Some(SolverStorage::Float64(solver)) = &self.storage else {
            return Err(text_error("solver precision is not float64"));
        };
        let points = points_xy
            .chunks_exact(2)
            .map(|point| [point[0], point[1]])
            .collect::<Vec<_>>();
        let mut output = vec![[0.0_f64; 2]; points.len()];
        solver
            .sample_velocity(&points, &mut output)
            .map_err(|error| js_error(&error))?;
        Ok(output.into_iter().flatten().collect())
    }

    pub fn diagnostics_json(&self) -> Result<String, JsValue> {
        let diagnostics = match &self.storage {
            Some(SolverStorage::Float32(solver)) => solver.diagnostics(),
            Some(SolverStorage::Float64(solver)) => solver.diagnostics(),
            None => return Err(text_error("solver is disposed")),
        }
        .map_err(|error| js_error(&error))?;
        Ok(json!({"stateRevision": diagnostics.state_revision, "values": diagnostics.values, "evidence": diagnostics.evidence, "warnings": diagnostics.warnings}).to_string())
    }

    pub fn export_state_json(&self) -> Result<String, JsValue> {
        match &self.storage {
            Some(SolverStorage::Float32(solver)) => {
                serde_json::to_string(&solver.export_state().map_err(|error| js_error(&error))?)
                    .map_err(|error| text_error(error.to_string()))
            }
            Some(SolverStorage::Float64(solver)) => {
                serde_json::to_string(&solver.export_state().map_err(|error| js_error(&error))?)
                    .map_err(|error| text_error(error.to_string()))
            }
            None => Err(text_error("solver is disposed")),
        }
    }

    pub fn export_state_metadata_json(&self) -> Result<String, JsValue> {
        let mut value = match &self.storage {
            Some(SolverStorage::Float32(solver)) => {
                serde_json::to_value(solver.export_state().map_err(|error| js_error(&error))?)
            }
            Some(SolverStorage::Float64(solver)) => {
                serde_json::to_value(solver.export_state().map_err(|error| js_error(&error))?)
            }
            None => return Err(text_error("solver is disposed")),
        }
        .map_err(|error| text_error(error.to_string()))?;
        let object = value
            .as_object_mut()
            .ok_or_else(|| text_error("canonical state did not serialize as an object"))?;
        object.remove("velocity");
        object.remove("density");
        Ok(value.to_string())
    }

    pub fn export_velocity_f32(&self) -> Result<Vec<f32>, JsValue> {
        let Some(SolverStorage::Float32(solver)) = &self.storage else {
            return Err(text_error("solver precision is not float32"));
        };
        let state = solver.export_state().map_err(|error| js_error(&error))?;
        Ok(state.velocity.into_iter().flatten().collect())
    }

    pub fn export_velocity_f64(&self) -> Result<Vec<f64>, JsValue> {
        let Some(SolverStorage::Float64(solver)) = &self.storage else {
            return Err(text_error("solver precision is not float64"));
        };
        let state = solver.export_state().map_err(|error| js_error(&error))?;
        Ok(state.velocity.into_iter().flatten().collect())
    }

    pub fn import_state_f32_json(
        &mut self,
        metadata_json: &str,
        velocity_xy: &[f32],
    ) -> Result<String, JsValue> {
        let metadata: CanonicalWire =
            serde_json::from_str(metadata_json).map_err(|error| text_error(error.to_string()))?;
        let state = CanonicalFlowState2 {
            bounds: metadata.bounds,
            resolution: metadata.resolution,
            periodic_axes: metadata.periodic_axes,
            time: metadata.time,
            angle_degrees: metadata.angle_degrees,
            angular_velocity_degrees: metadata.angular_velocity_degrees,
            geometry: metadata.geometry,
            producer: metadata.producer,
            source_solver: metadata.source_solver,
            velocity: velocity_xy
                .chunks_exact(2)
                .map(|value| [value[0], value[1]])
                .collect(),
            density: None,
        };
        let control = ControlState {
            time: state.time,
            angle_degrees: state.angle_degrees,
            angular_velocity_degrees: state.angular_velocity_degrees,
        };
        let Some(SolverStorage::Float32(solver)) = self.storage.as_mut() else {
            return Err(text_error("solver precision is not float32"));
        };
        Ok(import_json(&solver.import_state(&state, control)))
    }

    pub fn import_state_f64_json(
        &mut self,
        metadata_json: &str,
        velocity_xy: &[f64],
    ) -> Result<String, JsValue> {
        let metadata: CanonicalWire =
            serde_json::from_str(metadata_json).map_err(|error| text_error(error.to_string()))?;
        let state = CanonicalFlowState2 {
            bounds: metadata.bounds,
            resolution: metadata.resolution,
            periodic_axes: metadata.periodic_axes,
            time: metadata.time,
            angle_degrees: metadata.angle_degrees,
            angular_velocity_degrees: metadata.angular_velocity_degrees,
            geometry: metadata.geometry,
            producer: metadata.producer,
            source_solver: metadata.source_solver,
            velocity: velocity_xy
                .chunks_exact(2)
                .map(|value| [value[0], value[1]])
                .collect(),
            density: None,
        };
        let control = ControlState {
            time: state.time,
            angle_degrees: state.angle_degrees,
            angular_velocity_degrees: state.angular_velocity_degrees,
        };
        let Some(SolverStorage::Float64(solver)) = self.storage.as_mut() else {
            return Err(text_error("solver precision is not float64"));
        };
        Ok(import_json(&solver.import_state(&state, control)))
    }

    pub fn dispose(&mut self) {
        self.storage = None;
    }
}
