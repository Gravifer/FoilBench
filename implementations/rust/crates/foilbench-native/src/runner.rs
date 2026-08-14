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
    EvidenceValue, FlowScalar, FlowSolver, LbmD2q9, NacaFoil, PicFlip, Precision, Scenario,
    StableFluids, StepReport,
};
use serde::Deserialize;
use serde_json::{Value, json};

use crate::{canonical_io::write_canonical, resources::ResourceResolver};

const RECOVERY_OBSERVATION_LIMIT: f64 = 4.0;
const RECOVERY_TIME_TOLERANCE: f64 = 1.0e-9;

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
    let mut configuration = json!({
        "initial_condition": options.get("initial_condition").and_then(Value::as_str).unwrap_or("freestream"),
        "stable_advection": options.get("stable_advection").and_then(Value::as_str).unwrap_or("maccormack"),
        "stable_face_advection": options.get("stable_face_advection").and_then(Value::as_bool).unwrap_or(false),
        "stable_cfl": options.get("stable_cfl").and_then(Value::as_f64).unwrap_or(0.7),
        "pressure_tolerance": options.get("pressure_tolerance").and_then(Value::as_f64).unwrap_or(1.0e-5),
        "pressure_max_iterations": options.get("pressure_max_iterations").and_then(Value::as_u64).unwrap_or(640),
        "pic_flip_blend": options.get("pic_flip_blend").and_then(Value::as_f64).unwrap_or(0.95),
        "pic_population_interval": options.get("pic_population_interval").and_then(Value::as_u64).unwrap_or(8),
        "pic_cfl": options.get("pic_cfl").and_then(Value::as_f64).unwrap_or(0.75),
    });
    let object = configuration
        .as_object_mut()
        .expect("solver configuration is an object");
    for key in ["mac_maximum_divergence_linf", "mac_maximum_solid_leakage"] {
        if let Some(value) = options.get(key) {
            object.insert(key.into(), value.clone());
        }
    }
    configuration
}

fn evidence_number(report: &StepReport, key: &str, fallback: f64) -> f64 {
    match report.evidence.get(key) {
        Some(EvidenceValue::Number(value)) => *value,
        _ => fallback,
    }
}

fn recovery_window(scenario: &Scenario, duration: f64) -> Option<(f64, f64)> {
    let controls = scenario.controls();
    let initial = controls.first()?.angle_degrees;
    if (controls.last()?.angle_degrees - initial).abs() > 1.0e-9 {
        return None;
    }
    let changed = controls
        .iter()
        .enumerate()
        .filter_map(|(index, control)| {
            ((control.angle_degrees - initial).abs() > 1.0e-9).then_some(index)
        })
        .collect::<Vec<_>>();
    let first = *changed.first()?;
    let last = *changed.last()?;
    if first == 0 || last + 1 >= controls.len() {
        return None;
    }
    let baseline_end = controls[first - 1].time;
    let recovery_start = controls[last + 1].time;
    (baseline_end < recovery_start && recovery_start < duration)
        .then_some((baseline_end, recovery_start))
}

fn recovery_measurement(elapsed: Option<f64>, duration: f64, recovery_start: f64) -> (bool, f64) {
    let limit = RECOVERY_OBSERVATION_LIMIT.min((duration - recovery_start).max(0.0));
    let observed = elapsed.is_some_and(|value| value <= limit + RECOVERY_TIME_TOLERANCE);
    let reported = if observed {
        elapsed.map_or(limit, |value| value.min(limit))
    } else {
        limit
    };
    (observed, reported)
}

fn wake_probe_metrics(
    samples: &[f64],
    sample_dt: f64,
    chord: f64,
    freestream_speed: f64,
) -> Result<BTreeMap<String, f64>, String> {
    if samples.len() < 8
        || !(sample_dt > 0.0 && chord > 0.0 && freestream_speed > 0.0)
        || !samples.iter().all(|value| value.is_finite())
    {
        return Err("wake probe requires finite samples and positive scales".into());
    }
    let count = samples.len();
    let mean = samples.iter().sum::<f64>() / count as f64;
    let centered = samples
        .iter()
        .enumerate()
        .map(|(index, sample)| {
            let window = 0.5
                * (1.0 - (2.0 * std::f64::consts::PI * index as f64 / (count - 1) as f64).cos());
            ((*sample - mean), (*sample - mean) * window)
        })
        .collect::<Vec<_>>();
    let transverse_rms =
        (centered.iter().map(|(value, _)| value * value).sum::<f64>() / count as f64).sqrt();
    let mut total_power = 0.0;
    let mut dominant_power = 0.0;
    let mut dominant_index = 0_usize;
    for frequency in 1..=count / 2 {
        let mut real = 0.0;
        let mut imaginary = 0.0;
        for (index, (_, value)) in centered.iter().enumerate() {
            let phase = 2.0 * std::f64::consts::PI * frequency as f64 * index as f64 / count as f64;
            real += value * phase.cos();
            imaginary -= value * phase.sin();
        }
        let power = real.mul_add(real, imaginary * imaginary);
        total_power += power;
        if power > dominant_power {
            dominant_power = power;
            dominant_index = frequency;
        }
    }
    let frequency_resolution = 1.0 / (count as f64 * sample_dt);
    let dominant_frequency = if total_power <= f64::MIN_POSITIVE {
        0.0
    } else {
        dominant_index as f64 * frequency_resolution
    };
    Ok(BTreeMap::from([
        ("wake_probe_samples".into(), count as f64),
        ("wake_frequency_resolution".into(), frequency_resolution),
        ("wake_transverse_rms".into(), transverse_rms),
        (
            "wake_mixing_index".into(),
            transverse_rms / freestream_speed,
        ),
        ("wake_dominant_frequency".into(), dominant_frequency),
        (
            "wake_strouhal_number".into(),
            dominant_frequency * chord / freestream_speed,
        ),
        (
            "wake_dominant_power_fraction".into(),
            if total_power <= f64::MIN_POSITIVE {
                0.0
            } else {
                dominant_power / total_power
            },
        ),
    ]))
}

fn finite_json(value: &Value) -> bool {
    match value {
        Value::Number(number) => number.as_f64().is_some_and(f64::is_finite),
        Value::Array(values) => values.iter().all(finite_json),
        Value::Object(values) => values.values().all(finite_json),
        _ => true,
    }
}

fn semantic_identity_equal(left: &Value, right: &Value, tolerance: f64) -> bool {
    match (left, right) {
        (Value::Number(a), Value::Number(b)) => a
            .as_f64()
            .zip(b.as_f64())
            .is_some_and(|(a, b)| (a - b).abs() <= tolerance * a.abs().max(b.abs()).max(1.0)),
        (Value::Array(a), Value::Array(b)) => {
            a.len() == b.len()
                && a.iter()
                    .zip(b)
                    .all(|(a, b)| semantic_identity_equal(a, b, tolerance))
        }
        (Value::Object(a), Value::Object(b)) => {
            a.len() == b.len()
                && a.iter().all(|(key, value)| {
                    b.get(key)
                        .is_some_and(|other| semantic_identity_equal(value, other, tolerance))
                })
        }
        _ => left == right,
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
        "pic-flip" => Box::new(PicFlip::<T>::new("native")),
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
    let mut wake_probe = Vec::new();
    let recovery = recovery_window(scenario, matrix.duration);
    let mut recovery_baseline = None;
    let mut recovery_elapsed = None;
    let probe = [[
        T::from_f64(
            (scenario.foil().pivot[0] + 1.5 * scenario.foil().chord).min(
                scenario.bounds()[0][1]
                    - 0.5 * (scenario.bounds()[0][1] - scenario.bounds()[0][0])
                        / scenario.resolution()[0] as f64,
            ),
        ),
        T::from_f64(scenario.foil().pivot[1]),
    ]];
    let mut probe_velocity = [[T::from_f64(0.0); 2]];
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
        if simulated >= 0.5 * matrix.duration {
            solver
                .sample_velocity(&probe, &mut probe_velocity)
                .map_err(|error| error.to_string())?;
            wake_probe.push(probe_velocity[0][1].to_f64());
        }
        if let Some((baseline_end, recovery_start)) = recovery {
            let crossed_baseline = recovery_baseline.is_none() && simulated >= baseline_end;
            let observing_recovery = recovery_baseline.is_some()
                && recovery_elapsed.is_none()
                && simulated >= recovery_start;
            if crossed_baseline || observing_recovery {
                let transient = solver.diagnostics().map_err(|error| error.to_string())?;
                let wake = *transient
                    .values
                    .get("wake_width")
                    .ok_or("diagnostics omit wake_width")?;
                let recirculation = *transient
                    .values
                    .get("recirculation_area")
                    .ok_or("diagnostics omit recirculation_area")?;
                if crossed_baseline {
                    recovery_baseline = Some((wake, recirculation));
                } else if let Some((baseline_wake, baseline_recirculation)) = recovery_baseline {
                    let dx = (scenario.bounds()[0][1] - scenario.bounds()[0][0])
                        / scenario.resolution()[0] as f64;
                    let dy = (scenario.bounds()[1][1] - scenario.bounds()[1][0])
                        / scenario.resolution()[1] as f64;
                    if wake <= (1.25 * baseline_wake).max(2.0 * dy)
                        && recirculation <= (1.25 * baseline_recirculation).max(2.0 * dx * dy)
                    {
                        recovery_elapsed = Some(simulated - recovery_start);
                    }
                }
            }
        }
    }
    let report = last_step.ok_or("benchmark duration produced no completed steps")?;
    let diagnostics = solver.diagnostics().map_err(|error| error.to_string())?;
    let mut diagnostic_values = diagnostics.values.clone();
    if wake_probe.len() >= 8 {
        diagnostic_values.extend(wake_probe_metrics(
            &wake_probe,
            scenario.output_dt(),
            scenario.foil().chord,
            scenario.freestream()[0]
                .hypot(scenario.freestream()[1])
                .max(1.0e-12),
        )?);
    }
    let mut warnings = report.warnings.clone();
    warnings.extend(diagnostics.warnings.clone());
    if let (Some((baseline_end, recovery_start)), Some(_)) = (recovery, recovery_baseline) {
        let (observed, reported_elapsed) =
            recovery_measurement(recovery_elapsed, matrix.duration, recovery_start);
        diagnostic_values.extend(BTreeMap::from([
            ("recovery_baseline_time".into(), baseline_end),
            ("recovery_start_time".into(), recovery_start),
            ("recovery_observed".into(), if observed { 1.0 } else { 0.0 }),
            ("recovery_elapsed".into(), reported_elapsed),
        ]));
        if !observed {
            warnings
                .push("wake recovery was not observed; recovery_elapsed is right-censored".into());
        }
    }
    let total_wall = step_seconds.iter().sum::<f64>();
    let cells = scenario.resolution().iter().product::<usize>();
    let particle_count = diagnostics
        .values
        .get("particle_count")
        .copied()
        .unwrap_or(0.0);
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
        "foil": {
            "naca": scenario.foil().naca,
            "chord": scenario.foil().chord,
            "pivot": scenario.foil().pivot,
        },
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
        "particle_updates_per_second": particle_count * total_substeps as f64 / total_wall,
        "peak_rss_bytes": null,
        "memory_measurement": "unavailable",
        "runtime_startup_seconds": 0.0,
        "worker_startup_seconds": null,
        "substeps": total_substeps,
        "final_state_revision": solver.state_revision(),
        "diagnostic_state_revision": diagnostics.state_revision,
        "last_step": step_json(&report),
        "diagnostics": diagnostic_values,
        "success": true,
        "failure": null,
        "warnings": warnings,
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
    for key in ["solver", "benchmark_matrix_id", "git_commit"] {
        if object
            .get(key)
            .and_then(Value::as_str)
            .is_none_or(str::is_empty)
        {
            return Err(format!("benchmark artifact lacks {key}"));
        }
    }
    let version = object
        .get("schema_version")
        .and_then(Value::as_u64)
        .unwrap_or(0);
    if version == 1 {
        if object
            .get("language")
            .and_then(Value::as_str)
            .is_none_or(str::is_empty)
        {
            return Err("benchmark artifact lacks language".into());
        }
    } else if version == 2 {
        for key in ["implementation", "execution_target"] {
            if object
                .get(key)
                .and_then(Value::as_str)
                .is_none_or(str::is_empty)
            {
                return Err(format!("benchmark artifact lacks {key}"));
            }
        }
    } else {
        return Err("unsupported benchmark artifact schema version".into());
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
    let tolerance = if object.get("precision").and_then(Value::as_str) == Some("float32") {
        1.0e-6
    } else {
        1.0e-12
    } * requested.max(1.0);
    if (requested - simulated).abs() > tolerance {
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
            .any(|solver| !matches!(solver.as_str(), "stable-fluids" | "lbm-d2q9" | "pic-flip"))
    {
        return Err("this milestone implements stable-fluids, lbm-d2q9, and pic-flip".into());
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
        let implementation = artifact["implementation"]
            .as_str()
            .unwrap_or(artifact["language"].as_str().unwrap_or("unknown"));
        let default_target = if implementation == "typescript" {
            "browser-worker"
        } else {
            "native"
        };
        let producer = (
            implementation.to_string(),
            artifact["execution_target"]
                .as_str()
                .unwrap_or(default_target)
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
        "effective_reynolds",
        "solver_configuration",
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
            let tolerance = if artifact["precision"].as_str() == Some("float32") {
                2.0e-6
            } else {
                2.0e-12
            };
            for field in identities {
                if !semantic_identity_equal(&artifact[field], &reference[field], tolerance) {
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
    use foilbench_core::Scenario;

    use super::{percentile, recovery_measurement, recovery_window, wake_probe_metrics};

    #[test]
    fn percentile_interpolates_sorted_steps() {
        assert!((percentile(&[4.0, 1.0, 3.0, 2.0], 0.5) - 2.5).abs() < 1.0e-12);
    }

    #[test]
    fn scheduled_recovery_uses_the_declared_control_landmarks() {
        let scenario = Scenario::from_json(include_str!(
            "../../../../../scenarios/airfoil/default.json"
        ))
        .unwrap();
        assert_eq!(recovery_window(&scenario, 22.0), Some((3.0, 18.0)));
        assert_eq!(recovery_window(&scenario, 18.0), None);
    }

    #[test]
    fn scheduled_recovery_is_censored_at_the_explicit_observation_limit() {
        assert_eq!(recovery_measurement(Some(3.5), 30.0, 18.0), (true, 3.5));
        assert_eq!(recovery_measurement(Some(4.5), 30.0, 18.0), (false, 4.0));
        assert_eq!(recovery_measurement(None, 30.0, 18.0), (false, 4.0));
        assert_eq!(recovery_measurement(None, 20.0, 18.0), (false, 2.0));
    }

    #[test]
    fn wake_probe_metrics_are_finite_and_nonnegative() {
        let samples = (0..32)
            .map(|index| (2.0 * std::f64::consts::PI * f64::from(index) / 8.0).sin())
            .collect::<Vec<_>>();
        let metrics = wake_probe_metrics(&samples, 0.1, 1.0, 1.0).unwrap();
        assert!(metrics.values().all(|value| value.is_finite()));
        assert!(metrics["wake_mixing_index"] >= 0.0);
        assert!(metrics["wake_dominant_frequency"] > 0.0);
    }
}
