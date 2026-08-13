//! Native solver execution, benchmark artifacts, and offline comparison.

#![allow(
    clippy::cast_possible_truncation,
    clippy::cast_precision_loss,
    clippy::cast_sign_loss,
    clippy::too_many_lines
)]

use std::{
    collections::{BTreeMap, BTreeSet},
    fs,
    path::{Path, PathBuf},
    process::Command,
    time::{Instant, SystemTime, UNIX_EPOCH},
};

use foilbench_core::{
    EvidenceValue, FlowScalar, FlowSolver, LbmD2q9, NacaFoil, Precision, Scenario, StableFluids,
    StepReport,
};
use serde::Deserialize;
use serde_json::{Value, json};

use crate::{canonical_io::write_canonical, resources::ResourceResolver};

#[derive(Clone, Debug, Deserialize)]
struct BenchmarkMatrix {
    schema_version: u32,
    id: String,
    scenario: String,
    solvers: Vec<String>,
    resolutions: Vec<[usize; 2]>,
    duration: f64,
    repetitions: usize,
    save_snapshots: bool,
}

#[derive(Clone, Debug)]
pub struct BenchOptions {
    pub matrix: PathBuf,
    pub output: Option<PathBuf>,
    pub solver_filter: Option<String>,
}

fn percentile(values: &[f64], fraction: f64) -> f64 {
    if values.is_empty() {
        return 0.0;
    }
    let mut sorted = values.to_vec();
    sorted.sort_by(f64::total_cmp);
    let position = fraction * (sorted.len() - 1) as f64;
    let lower = position.floor() as usize;
    let upper = position.ceil() as usize;
    sorted[lower] + (sorted[upper] - sorted[lower]) * (position - lower as f64)
}

fn git_commit(root: &Path) -> String {
    Command::new("git")
        .args(["rev-parse", "HEAD"])
        .current_dir(root)
        .output()
        .ok()
        .filter(|output| output.status.success())
        .and_then(|output| String::from_utf8(output.stdout).ok())
        .map_or_else(|| "unknown".into(), |value| value.trim().into())
}

fn step_json(report: &StepReport) -> Value {
    json!({
        "requested_dt": report.requested_dt,
        "advanced_dt": report.advanced_dt,
        "substeps": report.substeps,
        "max_speed": report.max_speed,
        "state_revision": report.state_revision,
        "evidence": report.evidence,
        "warnings": report.warnings,
    })
}

fn solver_configuration(scenario: &Scenario) -> Value {
    let options = scenario.solver_options();
    json!({
        "initial_condition": options.get("initial_condition").and_then(Value::as_str).unwrap_or("freestream"),
        "stable_advection": options.get("stable_advection").and_then(Value::as_str).unwrap_or("maccormack"),
        "stable_face_advection": options.get("stable_face_advection").and_then(Value::as_bool).unwrap_or(false),
        "stable_cfl": options.get("stable_cfl").and_then(Value::as_f64).unwrap_or(0.7),
        "pressure_tolerance": options.get("pressure_tolerance").and_then(Value::as_f64).unwrap_or(1.0e-5),
        "pressure_max_iterations": options.get("pressure_max_iterations").and_then(Value::as_u64).unwrap_or(640),
        "pic_flip_blend": options.get("pic_flip_blend").and_then(Value::as_f64).unwrap_or(0.95),
        "pic_population_interval": options.get("pic_population_interval").and_then(Value::as_u64).unwrap_or(8),
        "pic_cfl": options.get("pic_cfl").and_then(Value::as_f64).unwrap_or(0.75),
    })
}

fn evidence_number(report: &StepReport, key: &str, fallback: f64) -> f64 {
    match report.evidence.get(key) {
        Some(EvidenceValue::Number(value)) => *value,
        _ => fallback,
    }
}

fn finite_json(value: &Value) -> bool {
    match value {
        Value::Number(number) => number.as_f64().is_some_and(f64::is_finite),
        Value::Array(values) => values.iter().all(finite_json),
        Value::Object(values) => values.values().all(finite_json),
        _ => true,
    }
}

fn run_typed<T: FlowScalar>(
    root: &Path,
    matrix: &BenchmarkMatrix,
    scenario: &Scenario,
    repetition: usize,
    destination: &Path,
    solver_id: &str,
) -> Result<Value, String> {
    let geometry = NacaFoil::new(scenario.foil().clone()).map_err(str::to_string)?;
    let mut solver: Box<dyn FlowSolver<T>> = match solver_id {
        "stable-fluids" => Box::new(StableFluids::<T>::new("native")),
        "lbm-d2q9" => Box::new(LbmD2q9::<T>::new("native")),
        _ => return Err(format!("unsupported Rust solver {solver_id}")),
    };
    let initialization_started = Instant::now();
    solver
        .initialize(scenario, &geometry, scenario.seed())
        .map_err(|error| error.to_string())?;
    let initialization_seconds = initialization_started.elapsed().as_secs_f64();
    let cold_started = Instant::now();
    solver
        .advance(
            scenario.control_at(scenario.output_dt()),
            scenario.output_dt(),
        )
        .map_err(|error| error.to_string())?;
    let cold_step_seconds = cold_started.elapsed().as_secs_f64();
    solver
        .initialize(scenario, &geometry, scenario.seed())
        .map_err(|error| error.to_string())?;

    let mut simulated = 0.0;
    let mut step_seconds = Vec::new();
    let mut total_substeps = 0_usize;
    let mut last_step = None;
    while simulated + 1.0e-12 < matrix.duration {
        let dt = scenario.output_dt().min(matrix.duration - simulated);
        simulated += dt;
        let started = Instant::now();
        let report = solver
            .advance(scenario.control_at(simulated), dt)
            .map_err(|error| error.to_string())?;
        step_seconds.push(started.elapsed().as_secs_f64());
        total_substeps += report.substeps;
        last_step = Some(report);
    }
    let report = last_step.ok_or("benchmark duration produced no completed steps")?;
    let diagnostics = solver.diagnostics().map_err(|error| error.to_string())?;
    let total_wall = step_seconds.iter().sum::<f64>();
    let cells = scenario.resolution().iter().product::<usize>();
    let effective_reynolds = evidence_number(&report, "effective_reynolds", scenario.reynolds());
    let precision = match T::PRECISION {
        Precision::Float32 => "float32",
        Precision::Float64 => "float64",
    };
    let artifact = json!({
        "schema_version": 2,
        "contract_id": "foilbench-phase3-v1",
        "contract_revision": 5,
        "benchmark_matrix_id": matrix.id,
        "scenario_id": scenario.id(),
        "repetition": repetition,
        "language": "rust",
        "implementation": "rust",
        "execution_target": "native",
        "solver": solver_id,
        "git_commit": git_commit(root),
        "machine": {"platform": std::env::consts::OS, "architecture": std::env::consts::ARCH},
        "precision": precision,
        "resolution": scenario.resolution(),
        "bounds": scenario.bounds(),
        "periodic_axes": scenario.periodic_axes(),
        "reynolds": scenario.reynolds(),
        "effective_reynolds": effective_reynolds,
        "solver_configuration": solver_configuration(scenario),
        "freestream": scenario.freestream(),
        "foil": scenario.foil(),
        "control_history": scenario.controls(),
        "requested_duration": matrix.duration,
        "simulated_duration": simulated,
        "output_dt": scenario.output_dt(),
        "seed": scenario.seed(),
        "initialization_seconds": initialization_seconds,
        "cold_step_seconds": cold_step_seconds,
        "step_seconds": step_seconds,
        "median_step_seconds": percentile(&step_seconds, 0.5),
        "p95_step_seconds": percentile(&step_seconds, 0.95),
        "simulated_seconds_per_wall_second": simulated / total_wall,
        "cell_updates_per_second": cells as f64 * total_substeps as f64 / total_wall,
        "particle_updates_per_second": 0.0,
        "peak_rss_bytes": null,
        "memory_measurement": "unavailable",
        "runtime_startup_seconds": 0.0,
        "worker_startup_seconds": null,
        "substeps": total_substeps,
        "final_state_revision": solver.state_revision(),
        "diagnostic_state_revision": diagnostics.state_revision,
        "last_step": step_json(&report),
        "diagnostics": diagnostics.values,
        "success": true,
        "failure": null,
        "warnings": report.warnings,
    });
    validate_result_semantics(&artifact)?;
    if matrix.save_snapshots {
        write_canonical(
            destination,
            &solver.export_state().map_err(|error| error.to_string())?,
        )?;
    }
    Ok(artifact)
}

fn validate_result_semantics(artifact: &Value) -> Result<(), String> {
    let object = artifact
        .as_object()
        .ok_or("benchmark artifact must be an object")?;
    for key in [
        "implementation",
        "execution_target",
        "solver",
        "benchmark_matrix_id",
        "git_commit",
    ] {
        if object
            .get(key)
            .and_then(Value::as_str)
            .is_none_or(str::is_empty)
        {
            return Err(format!("benchmark artifact lacks {key}"));
        }
    }
    if object.get("success") != Some(&Value::Bool(true)) {
        return Err("native runner emitted an unsuccessful success artifact".into());
    }
    let requested = object["requested_duration"]
        .as_f64()
        .ok_or("missing requested duration")?;
    let simulated = object["simulated_duration"]
        .as_f64()
        .ok_or("missing simulated duration")?;
    if (requested - simulated).abs() > 1.0e-10 * requested.max(1.0) {
        return Err("native runner did not complete the requested duration".into());
    }
    if object["final_state_revision"] != object["diagnostic_state_revision"] {
        return Err("native artifact contains stale diagnostics".into());
    }
    if !finite_json(artifact) {
        return Err("native artifact contains non-finite numbers".into());
    }
    Ok(())
}

/// Run a matrix for the implemented Rust solver repertoire.
///
/// # Errors
///
/// Returns an error for invalid resources, unsupported requested solvers, or
/// any failed numerical/artifact operation.
pub fn run_matrix(resolver: &ResourceResolver, options: &BenchOptions) -> Result<PathBuf, String> {
    let matrix_path = resolver.resolve(&options.matrix);
    let matrix: BenchmarkMatrix =
        serde_json::from_str(&fs::read_to_string(&matrix_path).map_err(|error| error.to_string())?)
            .map_err(|error| error.to_string())?;
    if matrix.schema_version != 1
        || matrix.repetitions == 0
        || matrix.resolutions.is_empty()
        || matrix.duration <= 0.0
    {
        return Err("invalid benchmark matrix semantics".into());
    }
    let solvers = options
        .solver_filter
        .as_ref()
        .map_or_else(|| matrix.solvers.clone(), |solver| vec![solver.clone()]);
    if solvers.is_empty()
        || solvers
            .iter()
            .any(|solver| !matches!(solver.as_str(), "stable-fluids" | "lbm-d2q9"))
    {
        return Err("this milestone implements stable-fluids and lbm-d2q9".into());
    }
    let timestamp = SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .map_err(|error| error.to_string())?
        .as_millis();
    let output = options.output.as_ref().map_or_else(
        || {
            resolver
                .root()
                .join("results/rust")
                .join(&matrix.id)
                .join(timestamp.to_string())
        },
        |path| resolver.resolve(path),
    );
    fs::create_dir_all(&output).map_err(|error| error.to_string())?;
    let base_document = fs::read_to_string(resolver.resolve(&matrix.scenario))
        .map_err(|error| error.to_string())?;
    let base: Value = serde_json::from_str(&base_document).map_err(|error| error.to_string())?;
    let mut summary = vec!["solver,resolution,repetition,median_step_seconds,p95_step_seconds,simulated_seconds_per_wall_second,success".to_string()];
    for solver_id in &solvers {
        for resolution in &matrix.resolutions {
            for repetition in 1..=matrix.repetitions {
                let mut selected = base.clone();
                selected["resolution"] = json!(resolution);
                let scenario = Scenario::from_json(
                    &serde_json::to_string(&selected).map_err(|error| error.to_string())?,
                )
                .map_err(|error| error.to_string())?;
                let stem = format!(
                    "{solver_id}-{}x{}-r{repetition}",
                    resolution[0], resolution[1]
                );
                let snapshot = output.join(format!("{stem}-state"));
                let artifact = match scenario.precision() {
                    Precision::Float32 => run_typed::<f32>(
                        resolver.root(),
                        &matrix,
                        &scenario,
                        repetition,
                        &snapshot,
                        solver_id,
                    )?,
                    Precision::Float64 => run_typed::<f64>(
                        resolver.root(),
                        &matrix,
                        &scenario,
                        repetition,
                        &snapshot,
                        solver_id,
                    )?,
                };
                fs::write(
                    output.join(format!("{stem}.json")),
                    format!(
                        "{}\n",
                        serde_json::to_string_pretty(&artifact)
                            .map_err(|error| error.to_string())?
                    ),
                )
                .map_err(|error| error.to_string())?;
                summary.push(format!(
                    "{solver_id},{}x{},{repetition},{},{},{},true",
                    resolution[0],
                    resolution[1],
                    artifact["median_step_seconds"],
                    artifact["p95_step_seconds"],
                    artifact["simulated_seconds_per_wall_second"]
                ));
            }
        }
    }
    fs::write(
        output.join("summary.csv"),
        format!("{}\n", summary.join("\n")),
    )
    .map_err(|error| error.to_string())?;
    Ok(output)
}

fn collect_json(directory: &Path, output: &mut Vec<Value>) -> Result<(), String> {
    for entry in fs::read_dir(directory).map_err(|error| error.to_string())? {
        let path = entry.map_err(|error| error.to_string())?.path();
        if path.is_dir() {
            collect_json(&path, output)?;
        } else if path
            .extension()
            .is_some_and(|extension| extension == "json")
        {
            let value: Value =
                serde_json::from_str(&fs::read_to_string(path).map_err(|error| error.to_string())?)
                    .map_err(|error| error.to_string())?;
            if value.get("benchmark_matrix_id").is_some() {
                output.push(value);
            }
        }
    }
    Ok(())
}

/// Validate semantic identity and optional producer completeness offline.
///
/// # Errors
///
/// Returns an error for malformed/failed artifacts, duplicates, or absent
/// required `implementation/execution_target` producers.
pub fn compare_results(
    directory: &Path,
    required_producers: &[(String, String)],
) -> Result<usize, String> {
    let mut artifacts = Vec::new();
    collect_json(directory, &mut artifacts)?;
    if artifacts.is_empty() {
        return Err("comparison directory contains no benchmark artifacts".into());
    }
    let mut cells = BTreeSet::new();
    let mut observed = BTreeSet::new();
    for artifact in &artifacts {
        validate_result_semantics(artifact)?;
        let producer = (
            artifact["implementation"]
                .as_str()
                .unwrap_or(artifact["language"].as_str().unwrap_or("unknown"))
                .to_string(),
            artifact["execution_target"]
                .as_str()
                .unwrap_or("native")
                .to_string(),
        );
        observed.insert(producer.clone());
        let key = (
            artifact["benchmark_matrix_id"].to_string(),
            producer,
            artifact["solver"].to_string(),
            artifact["resolution"].to_string(),
            artifact["repetition"].to_string(),
        );
        if !cells.insert(key) {
            return Err("comparison contains a duplicate producer/matrix cell".into());
        }
    }
    for required in required_producers {
        if !observed.contains(required) {
            return Err(format!(
                "comparison is missing required producer {}/{}",
                required.0, required.1
            ));
        }
    }
    let identities = [
        "bounds",
        "periodic_axes",
        "reynolds",
        "freestream",
        "foil",
        "control_history",
        "requested_duration",
        "output_dt",
        "seed",
    ];
    let mut grouped: BTreeMap<(String, String, String, String), &Value> = BTreeMap::new();
    for artifact in &artifacts {
        let key = (
            artifact["benchmark_matrix_id"].to_string(),
            artifact["solver"].to_string(),
            artifact["resolution"].to_string(),
            artifact["repetition"].to_string(),
        );
        if let Some(reference) = grouped.get(&key) {
            for field in identities {
                if artifact[field] != reference[field] {
                    return Err(format!("physical identity differs for field {field}"));
                }
            }
        } else {
            grouped.insert(key, artifact);
        }
    }
    Ok(artifacts.len())
}

#[cfg(test)]
mod tests {
    use super::percentile;

    #[test]
    fn percentile_interpolates_sorted_steps() {
        assert!((percentile(&[4.0, 1.0, 3.0, 2.0], 0.5) - 2.5).abs() < 1.0e-12);
    }
}
