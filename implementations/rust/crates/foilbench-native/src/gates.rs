//! Representative native Rust acceptance gates.

use std::{fs, path::Path, time::Instant};

use foilbench_core::{
    ControlState, FlowSolver, LbmD2q9, NacaFoil, PicFlip, Scenario, StableFluids,
};
use serde_json::{Value, json};

use crate::resources::ResourceResolver;

const SOLVERS: [&str; 3] = ["stable-fluids", "lbm-d2q9", "pic-flip"];

fn scenario_at_resolution(
    resolver: &ResourceResolver,
    path: &str,
    resolution: [usize; 2],
) -> Result<Scenario, String> {
    let document = fs::read_to_string(resolver.resolve(path)).map_err(|error| error.to_string())?;
    let mut value: Value = serde_json::from_str(&document).map_err(|error| error.to_string())?;
    value["resolution"] = json!(resolution);
    value["precision"] = json!("float32");
    Scenario::from_json(&serde_json::to_string(&value).map_err(|error| error.to_string())?)
        .map_err(|error| error.to_string())
}

fn solver(id: &str) -> Result<Box<dyn FlowSolver<f32>>, String> {
    match id {
        "stable-fluids" => Ok(Box::new(StableFluids::<f32>::new("native"))),
        "lbm-d2q9" => Ok(Box::new(LbmD2q9::<f32>::new("native"))),
        "pic-flip" => Ok(Box::new(PicFlip::<f32>::new("native"))),
        _ => Err(format!("unsupported solver {id}")),
    }
}

fn initialize(
    id: &str,
    scenario: &Scenario,
    geometry: &NacaFoil,
) -> Result<Box<dyn FlowSolver<f32>>, String> {
    let mut selected = solver(id)?;
    selected
        .initialize(scenario, geometry, scenario.seed())
        .map_err(|error| error.to_string())?;
    Ok(selected)
}

fn startup(resolver: &ResourceResolver) -> Result<Value, String> {
    let scenario = scenario_at_resolution(resolver, "scenarios/airfoil/default.json", [160, 96])?;
    let geometry = NacaFoil::new(scenario.foil().clone()).map_err(str::to_string)?;
    let mut evidence = serde_json::Map::new();
    for id in SOLVERS {
        let started = Instant::now();
        let mut selected = initialize(id, &scenario, &geometry)?;
        let report = selected
            .advance(
                scenario.control_at(scenario.output_dt()),
                scenario.output_dt(),
            )
            .map_err(|error| error.to_string())?;
        let diagnostics = selected.diagnostics().map_err(|error| error.to_string())?;
        if diagnostics.state_revision != report.state_revision {
            return Err(format!("{id} published stale startup diagnostics"));
        }
        evidence.insert(
            id.into(),
            json!({
                "wall_seconds": started.elapsed().as_secs_f64(),
                "substeps": report.substeps,
                "maximum_speed": report.max_speed,
                "state_revision": report.state_revision,
                "finite": true
            }),
        );
    }
    Ok(Value::Object(evidence))
}

fn preview(resolver: &ResourceResolver) -> Result<Value, String> {
    let scenario = scenario_at_resolution(resolver, "scenarios/airfoil/default.json", [160, 96])?;
    let geometry = NacaFoil::new(scenario.foil().clone()).map_err(str::to_string)?;
    let mut evidence = serde_json::Map::new();
    for id in SOLVERS {
        let mut selected = initialize(id, &scenario, &geometry)?;
        let dt = scenario.output_dt();
        selected
            .advance(scenario.control_at(dt), dt)
            .map_err(|error| error.to_string())?;
        let mut elapsed = Vec::new();
        for step in 2..=7 {
            let started = Instant::now();
            selected
                .advance(scenario.control_at(f64::from(step) * dt), dt)
                .map_err(|error| error.to_string())?;
            elapsed.push(started.elapsed().as_secs_f64());
        }
        elapsed.sort_by(f64::total_cmp);
        let median = elapsed[elapsed.len() / 2];
        evidence.insert(
            id.into(),
            json!({"median_step_seconds": median, "warmed_steps_per_second": 1.0 / median, "hosted_ci_records_only": true}),
        );
    }
    Ok(Value::Object(evidence))
}

fn warm_switch(resolver: &ResourceResolver) -> Result<Value, String> {
    let scenario = scenario_at_resolution(resolver, "scenarios/airfoil/default.json", [160, 96])?;
    let geometry = NacaFoil::new(scenario.foil().clone()).map_err(str::to_string)?;
    let mut accepted = 0_u32;
    let mut evidence = serde_json::Map::new();
    for angle in [14.0, 25.0] {
        for source_id in SOLVERS {
            for destination_id in SOLVERS {
                if source_id == destination_id {
                    continue;
                }
                let dt = scenario.output_dt();
                let control = ControlState {
                    time: dt,
                    angle_degrees: angle,
                    angular_velocity_degrees: 0.0,
                };
                let mut source = initialize(source_id, &scenario, &geometry)?;
                source
                    .advance(control, dt)
                    .map_err(|error| format!("{source_id} at {angle}: {error}"))?;
                let state = source.export_state().map_err(|error| error.to_string())?;
                let mut destination = initialize(destination_id, &scenario, &geometry)?;
                let outcome = destination.import_state(&state, control);
                if !outcome.accepted {
                    return Err(format!(
                        "{source_id}->{destination_id} at {angle} rejected: {:?}",
                        outcome.reason
                    ));
                }
                let next = ControlState {
                    time: 2.0 * dt,
                    ..control
                };
                destination.advance(next, dt).map_err(|error| {
                    format!("{source_id}->{destination_id} at {angle}: {error}")
                })?;
                accepted += 1;
                evidence.insert(
                    format!("{source_id}->{destination_id}@{angle}"),
                    json!({"import": "accepted", "tentative_step": "accepted", "time": 2.0 * dt}),
                );
            }
        }
    }
    if accepted != 12 {
        return Err(format!(
            "expected 12 directed angle cells, observed {accepted}"
        ));
    }
    Ok(json!({"accepted_cells": accepted, "cells": evidence}))
}

fn scheduled(resolver: &ResourceResolver) -> Result<Value, String> {
    let scenario = scenario_at_resolution(resolver, "scenarios/airfoil/default.json", [160, 96])?;
    let geometry = NacaFoil::new(scenario.foil().clone()).map_err(str::to_string)?;
    let mut selected = initialize("stable-fluids", &scenario, &geometry)?;
    let mut time = 0.0;
    let mut checkpoints = Vec::new();
    for target in [5.0_f64, 10.0, 22.0] {
        while time + 1.0e-12 < target {
            let dt = scenario.output_dt().min(target - time);
            time += dt;
            selected
                .advance(scenario.control_at(time), dt)
                .map_err(|error| error.to_string())?;
        }
        let state = selected.export_state().map_err(|error| error.to_string())?;
        let diagnostics = selected.diagnostics().map_err(|error| error.to_string())?;
        if (state.time - target).abs() > 1.0e-9
            || (state.angle_degrees - scenario.control_at(target).angle_degrees).abs() > 1.0e-8
            || diagnostics.state_revision != selected.state_revision()
        {
            return Err(format!("scheduled checkpoint {target} is inconsistent"));
        }
        checkpoints.push(json!({
            "time": target,
            "angle_degrees": state.angle_degrees,
            "state_revision": selected.state_revision(),
            "finite": true
        }));
    }
    Ok(json!({"checkpoints": checkpoints}))
}

/// Execute one native representative gate and return its measurements.
///
/// # Errors
///
/// Returns an error when a solver, import, checkpoint, or numerical invariant
/// fails.
pub fn run_gate(
    resolver: &ResourceResolver,
    gate: &str,
    output: Option<&Path>,
) -> Result<Value, String> {
    let measurements = match gate {
        "startup" => startup(resolver)?,
        "preview" => preview(resolver)?,
        "warm-switch" => warm_switch(resolver)?,
        "scheduled" => scheduled(resolver)?,
        _ => return Err(format!("unknown native gate {gate}")),
    };
    if let Some(path) = output {
        let destination = resolver.resolve(path);
        if let Some(parent) = destination.parent() {
            fs::create_dir_all(parent).map_err(|error| error.to_string())?;
        }
        fs::write(
            destination,
            format!(
                "{}\n",
                serde_json::to_string_pretty(&measurements).map_err(|error| error.to_string())?
            ),
        )
        .map_err(|error| error.to_string())?;
    }
    Ok(measurements)
}
