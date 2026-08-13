//! Native Rust chaotic-wake sweep and symmetric paired-sensitivity experiments.

#![allow(
    clippy::cast_possible_truncation,
    clippy::cast_precision_loss,
    clippy::cast_sign_loss,
    clippy::too_many_lines
)]

use std::{fs, path::PathBuf, time::Instant};

use foilbench_core::{CanonicalFlowState2, FlowSolver, NacaFoil, Scenario, StableFluids};
use serde::Deserialize;
use serde_json::{Value, json};

use crate::resources::ResourceResolver;

#[derive(Clone, Debug, Deserialize)]
struct CaseWire {
    reynolds: f64,
    angle_degrees: f64,
    resolution: [usize; 2],
}

#[derive(Clone, Debug, Deserialize)]
struct SweepWire {
    duration: f64,
    burn_in: f64,
    cases: Vec<CaseWire>,
}

#[derive(Clone, Debug, Deserialize)]
struct PairedWire {
    duration: f64,
    epsilon: f64,
    case: CaseWire,
}

#[derive(Clone, Debug, Deserialize)]
struct CasesWire {
    scenario: String,
    sweep: SweepWire,
    sensitivity: PairedWire,
    initialization_preflight: PairedWire,
}

fn selected_scenario(base: &Value, selected: &CaseWire, duration: f64) -> Result<Scenario, String> {
    let mut document = base.clone();
    document["id"] = json!(format!(
        "chaotic-wake-re{}-a{}-{}x{}",
        selected.reynolds, selected.angle_degrees, selected.resolution[0], selected.resolution[1]
    ));
    document["resolution"] = json!(selected.resolution);
    document["reynolds"] = json!(selected.reynolds);
    document["controls"] = json!([
        {"time": 0.0, "angle_degrees": selected.angle_degrees},
        {"time": duration, "angle_degrees": selected.angle_degrees},
    ]);
    document["duration"] = json!(duration);
    document["solver_options"]["stable_advection"] = json!("skew-rk2");
    Scenario::from_json(&serde_json::to_string(&document).map_err(|error| error.to_string())?)
        .map_err(|error| error.to_string())
}

fn spectrum(samples: &[f64]) -> (f64, f64, f64) {
    if samples.len() < 4 {
        return (0.0, 0.0, 0.0);
    }
    let mean = samples.iter().sum::<f64>() / samples.len() as f64;
    let mut powers = vec![0.0; samples.len() / 2 + 1];
    for (frequency, power) in powers.iter_mut().enumerate().skip(1) {
        let mut real = 0.0;
        let mut imaginary = 0.0;
        for (index, sample) in samples.iter().enumerate() {
            let window = 0.5
                - 0.5
                    * (2.0 * std::f64::consts::PI * index as f64 / (samples.len() - 1) as f64)
                        .cos();
            let phase = -2.0 * std::f64::consts::PI * frequency as f64 * index as f64
                / samples.len() as f64;
            let value = (*sample - mean) * window;
            real += value * phase.cos();
            imaginary += value * phase.sin();
        }
        *power = real * real + imaginary * imaginary;
    }
    let total = powers.iter().sum::<f64>();
    if total <= f64::EPSILON {
        return (0.0, 0.0, 0.0);
    }
    let mut entropy = 0.0;
    let mut dominant = 1;
    for index in 1..powers.len() {
        let probability = powers[index] / total;
        if probability > 0.0 {
            entropy -= probability * probability.ln();
        }
        if powers[index] > powers[dominant] {
            dominant = index;
        }
    }
    entropy /= ((powers.len() - 1).max(2) as f64).ln();
    let coherent = powers[dominant.saturating_sub(1).max(1)..=(dominant + 1).min(powers.len() - 1)]
        .iter()
        .sum::<f64>();
    (entropy, powers[dominant] / total, 1.0 - coherent / total)
}

fn decorrelation_time(samples: &[f64], dt: f64) -> f64 {
    if samples.is_empty() {
        return 0.0;
    }
    let mean = samples.iter().sum::<f64>() / samples.len() as f64;
    let centered = samples.iter().map(|value| value - mean).collect::<Vec<_>>();
    let variance = centered.iter().map(|value| value * value).sum::<f64>() / samples.len() as f64;
    if variance <= f64::EPSILON {
        return 0.0;
    }
    for lag in 0..samples.len() {
        let overlap = samples.len() - lag;
        let correlation = (0..overlap)
            .map(|index| centered[index] * centered[index + lag])
            .sum::<f64>()
            / (overlap as f64 * variance);
        if correlation < (-1.0_f64).exp() {
            return lag as f64 * dt;
        }
    }
    (samples.len() - 1) as f64 * dt
}

fn small_scale_fraction(state: &CanonicalFlowState2<f32>) -> f64 {
    let [nx, ny] = state.resolution;
    let dx = (state.bounds[0][1] - state.bounds[0][0]) / nx as f64;
    let dy = (state.bounds[1][1] - state.bounds[1][0]) / ny as f64;
    let mut omega = vec![0.0_f64; nx * ny];
    let mut total = 0.0;
    for y in 1..ny - 1 {
        for x in 1..nx - 1 {
            let left = state.velocity[y * nx + x - 1];
            let right = state.velocity[y * nx + x + 1];
            let bottom = state.velocity[(y - 1) * nx + x];
            let top = state.velocity[(y + 1) * nx + x];
            let value = f64::from(right[1] - left[1]) / (2.0 * dx)
                - f64::from(top[0] - bottom[0]) / (2.0 * dy);
            omega[y * nx + x] = value;
            total += value * value;
        }
    }
    if total <= f64::EPSILON {
        return 0.0;
    }
    let mut gradient = 0.0;
    for y in 2..ny - 2 {
        for x in 2..nx - 2 {
            let gx = 0.5 * (omega[y * nx + x + 1] - omega[y * nx + x - 1]);
            let gy = 0.5 * (omega[(y + 1) * nx + x] - omega[(y - 1) * nx + x]);
            gradient += gx * gx + gy * gy;
        }
    }
    gradient / total
}

fn sweep_case(
    base: &Value,
    selected: &CaseWire,
    duration: f64,
    burn_in: f64,
) -> Result<Value, String> {
    let scenario = selected_scenario(base, selected, duration)?;
    let geometry = NacaFoil::new(scenario.foil().clone()).map_err(str::to_string)?;
    let mut solver = StableFluids::<f32>::new("native");
    solver
        .initialize(&scenario, &geometry, scenario.seed())
        .map_err(|error| error.to_string())?;
    let probe = [[
        (scenario.foil().pivot[0] + 1.5 * scenario.foil().chord) as f32,
        scenario.foil().pivot[1] as f32,
    ]];
    let mut output = [[0.0_f32; 2]];
    let mut transverse = Vec::new();
    let mut enstrophy = Vec::new();
    let mut maximum_speed = 0.0_f64;
    let mut simulated = 0.0;
    let started = Instant::now();
    while simulated < duration - 1.0e-12 {
        let dt = scenario.output_dt().min(duration - simulated);
        simulated += dt;
        let report = solver
            .advance(scenario.control_at(simulated), dt)
            .map_err(|error| error.to_string())?;
        maximum_speed = maximum_speed.max(report.max_speed);
        if simulated >= burn_in {
            solver
                .sample_velocity(&probe, &mut output)
                .map_err(|error| error.to_string())?;
            transverse.push(f64::from(output[0][1]));
            enstrophy.push(
                *solver
                    .diagnostics()
                    .map_err(|error| error.to_string())?
                    .values
                    .get("enstrophy")
                    .unwrap_or(&0.0),
            );
        }
    }
    let probe_mean = transverse.iter().sum::<f64>() / transverse.len().max(1) as f64;
    let probe_rms = (transverse
        .iter()
        .map(|value| (value - probe_mean).powi(2))
        .sum::<f64>()
        / transverse.len().max(1) as f64)
        .sqrt();
    let enstrophy_mean = enstrophy.iter().sum::<f64>() / enstrophy.len().max(1) as f64;
    let enstrophy_variance = enstrophy
        .iter()
        .map(|value| (value - enstrophy_mean).powi(2))
        .sum::<f64>()
        / enstrophy.len().max(1) as f64;
    let (entropy, dominant, broadband) = spectrum(&transverse);
    let state = solver.export_state().map_err(|error| error.to_string())?;
    Ok(json!({
        "schema_version": 2,
        "contract_id": "foilbench-phase3-v1",
        "contract_revision": 5,
        "experiment": "chaotic-wake-sweep",
        "language": "rust",
        "implementation": "rust",
        "execution_target": "native",
        "solver": "stable-fluids",
        "scenario": scenario.id(),
        "parameters": {"reynolds": selected.reynolds, "angle_degrees": selected.angle_degrees, "resolution": selected.resolution, "duration": duration, "burn_in": burn_in},
        "metrics": {
            "probe_rms": probe_rms,
            "spectral_entropy": entropy,
            "dominant_power_fraction": dominant,
            "broadband_power_fraction": broadband,
            "decorrelation_time": decorrelation_time(&transverse, scenario.output_dt()),
            "enstrophy_mean": enstrophy_mean,
            "enstrophy_coefficient_of_variation": enstrophy_variance.sqrt() / enstrophy_mean.max(f64::EPSILON),
            "maximum_speed": maximum_speed,
            "vorticity_small_scale_fraction": small_scale_fraction(&state),
        },
        "wall_seconds": started.elapsed().as_secs_f64(),
    }))
}

fn wake_mask(scenario: &Scenario, geometry: &NacaFoil, angle: f64) -> Vec<bool> {
    let [nx, ny] = [scenario.resolution()[0], scenario.resolution()[1]];
    let dx = (scenario.bounds()[0][1] - scenario.bounds()[0][0]) / nx as f64;
    let dy = (scenario.bounds()[1][1] - scenario.bounds()[1][0]) / ny as f64;
    (0..ny)
        .flat_map(|y| {
            (0..nx).map(move |x| {
                let point = [
                    scenario.bounds()[0][0] + (x as f64 + 0.5) * dx,
                    scenario.bounds()[1][0] + (y as f64 + 0.5) * dy,
                ];
                point[0] > scenario.foil().pivot[0] && geometry.signed_distance(point, angle) > 0.0
            })
        })
        .collect()
}

fn wake_difference(first: &[[f32; 2]], second: &[[f32; 2]], wake: &[bool]) -> f64 {
    let mut sum = 0.0;
    let mut samples = 0;
    for ((left, right), selected) in first.iter().zip(second).zip(wake) {
        if *selected {
            for component in 0..2 {
                sum += f64::from(left[component] - right[component]).powi(2);
                samples += 1;
            }
        }
    }
    (sum / f64::from(samples.max(1))).sqrt()
}

fn exponential_fit(times: &[f64], differences: &[f64], initial: f64) -> (f64, f64, usize) {
    let selected = (0..times.len())
        .filter(|index| {
            differences[*index] >= 1.5 * initial
                && differences[*index] <= 0.02
                && differences[*index].is_finite()
        })
        .collect::<Vec<_>>();
    if selected.len() < 8 {
        return (0.0, 0.0, selected.len());
    }
    let mean_x = selected.iter().map(|index| times[*index]).sum::<f64>() / selected.len() as f64;
    let mean_y = selected
        .iter()
        .map(|index| differences[*index].ln())
        .sum::<f64>()
        / selected.len() as f64;
    let denominator = selected
        .iter()
        .map(|index| (times[*index] - mean_x).powi(2))
        .sum::<f64>();
    let slope = selected
        .iter()
        .map(|index| (times[*index] - mean_x) * (differences[*index].ln() - mean_y))
        .sum::<f64>()
        / denominator.max(f64::EPSILON);
    let intercept = mean_y - slope * mean_x;
    let residual = selected
        .iter()
        .map(|index| (differences[*index].ln() - (intercept + slope * times[*index])).powi(2))
        .sum::<f64>();
    let total = selected
        .iter()
        .map(|index| (differences[*index].ln() - mean_y).powi(2))
        .sum::<f64>();
    (
        slope,
        1.0 - residual / total.max(f64::EPSILON),
        selected.len(),
    )
}

fn sensitivity_case(base: &Value, selected: &PairedWire) -> Result<Value, String> {
    let scenario = selected_scenario(base, &selected.case, selected.duration)?;
    let geometry = NacaFoil::new(scenario.foil().clone()).map_err(str::to_string)?;
    let mut reference = StableFluids::<f32>::new("native");
    let mut perturbed = StableFluids::<f32>::new("native");
    reference
        .initialize(&scenario, &geometry, scenario.seed())
        .map_err(|error| error.to_string())?;
    perturbed
        .initialize(&scenario, &geometry, scenario.seed())
        .map_err(|error| error.to_string())?;
    let control = scenario.control_at(0.0);
    let base_state = reference
        .export_state()
        .map_err(|error| error.to_string())?;
    let reference_outcome = reference.import_state(&base_state, control);
    if !reference_outcome.accepted {
        return Err(format!(
            "reference import rejected: {:?}",
            reference_outcome.reason
        ));
    }
    let mut changed = base_state.clone();
    let [nx, ny] = changed.resolution;
    let dx = (changed.bounds[0][1] - changed.bounds[0][0]) / nx as f64;
    let dy = (changed.bounds[1][1] - changed.bounds[1][0]) / ny as f64;
    let mut stream = vec![0.0; nx * ny];
    let mut px = vec![0.0; nx * ny];
    let mut py = vec![0.0; nx * ny];
    for y in 0..ny {
        for x in 0..nx {
            let cell = y * nx + x;
            let cx = changed.bounds[0][0] + (x as f64 + 0.5) * dx;
            let cy = changed.bounds[1][0] + (y as f64 + 0.5) * dy;
            stream[cell] = (-((cx - 0.2) / 0.8).powi(2) - ((cy - 0.25) / 0.5).powi(2)).exp()
                * (2.0 * std::f64::consts::PI * (cx - changed.bounds[0][0]) / 1.3).sin()
                * (2.0 * std::f64::consts::PI * (cy - changed.bounds[1][0]) / 0.9).sin();
        }
    }
    let mut maximum = 0.0_f64;
    for y in 0..ny {
        for x in 0..nx {
            let cell = y * nx + x;
            let x0 = x.saturating_sub(1);
            let x1 = (x + 1).min(nx - 1);
            let y0 = y.saturating_sub(1);
            let y1 = (y + 1).min(ny - 1);
            px[cell] = (stream[y1 * nx + x] - stream[y0 * nx + x]) / ((y1 - y0).max(1) as f64 * dy);
            py[cell] =
                -(stream[y * nx + x1] - stream[y * nx + x0]) / ((x1 - x0).max(1) as f64 * dx);
            let point = [
                changed.bounds[0][0] + (x as f64 + 0.5) * dx,
                changed.bounds[1][0] + (y as f64 + 0.5) * dy,
            ];
            if geometry.signed_distance(point, selected.case.angle_degrees) <= 0.0 {
                px[cell] = 0.0;
                py[cell] = 0.0;
            }
            maximum = maximum.max(px[cell].hypot(py[cell]));
        }
    }
    for cell in 0..changed.velocity.len() {
        changed.velocity[cell][0] +=
            (selected.epsilon * px[cell] / maximum.max(f64::EPSILON)) as f32;
        changed.velocity[cell][1] +=
            (selected.epsilon * py[cell] / maximum.max(f64::EPSILON)) as f32;
    }
    let perturbed_outcome = perturbed.import_state(&changed, control);
    if !perturbed_outcome.accepted {
        return Err(format!(
            "perturbed import rejected: {:?}",
            perturbed_outcome.reason
        ));
    }
    let wake = wake_mask(&scenario, &geometry, selected.case.angle_degrees);
    let initial = wake_difference(
        &reference
            .export_state()
            .map_err(|error| error.to_string())?
            .velocity,
        &perturbed
            .export_state()
            .map_err(|error| error.to_string())?
            .velocity,
        &wake,
    );
    if !initial.is_finite() || initial <= 0.0 {
        return Err("paired reconstruction realized no finite separation".into());
    }
    let mut times = Vec::new();
    let mut differences = Vec::new();
    let mut simulated = 0.0;
    let started = Instant::now();
    while simulated < selected.duration - 1.0e-12 {
        let dt = scenario.output_dt().min(selected.duration - simulated);
        simulated += dt;
        let next = scenario.control_at(simulated);
        reference
            .advance(next, dt)
            .map_err(|error| error.to_string())?;
        perturbed
            .advance(next, dt)
            .map_err(|error| error.to_string())?;
        times.push(simulated);
        differences.push(wake_difference(
            &reference
                .export_state()
                .map_err(|error| error.to_string())?
                .velocity,
            &perturbed
                .export_state()
                .map_err(|error| error.to_string())?
                .velocity,
            &wake,
        ));
    }
    let final_difference = *differences.last().unwrap_or(&initial);
    let maximum_difference = differences.iter().copied().fold(initial, f64::max);
    let (exponent, r_squared, samples) = exponential_fit(&times, &differences, initial);
    Ok(json!({
        "schema_version": 2,
        "contract_id": "foilbench-phase3-v1",
        "contract_revision": 5,
        "experiment": "chaotic-wake-sensitivity",
        "language": "rust",
        "implementation": "rust",
        "execution_target": "native",
        "solver": "stable-fluids",
        "scenario": scenario.id(),
        "parameters": {"reynolds": selected.case.reynolds, "angle_degrees": selected.case.angle_degrees, "resolution": selected.case.resolution, "duration": selected.duration, "epsilon": selected.epsilon},
        "metrics": {"initial_wake_rms_difference": initial, "final_wake_rms_difference": final_difference, "maximum_wake_rms_difference": maximum_difference, "amplification": maximum_difference / initial, "finite_time_exponent": exponent, "exponential_fit_r_squared": r_squared, "exponential_fit_samples": samples},
        "initialization": {"reference_import_status": "accepted", "perturbed_import_status": "accepted", "authoritative_angle_degrees": selected.case.angle_degrees, "requested_epsilon": selected.epsilon, "realized_post_import_wake_rms_difference": initial, "realized_to_requested_ratio": initial / selected.epsilon},
        "series": {"times": times, "wake_rms_differences": differences},
        "wall_seconds": started.elapsed().as_secs_f64(),
    }))
}

/// Run one of the shared native Rust chaos evidence modes.
///
/// # Errors
///
/// Returns an error when resources, numerical evolution, or output fail.
pub fn run_chaos(
    resolver: &ResourceResolver,
    mode: &str,
    output: Option<PathBuf>,
) -> Result<Value, String> {
    let cases: CasesWire = serde_json::from_str(
        &fs::read_to_string(resolver.resolve("spec/conformance/chaotic-wake-cases.json"))
            .map_err(|error| error.to_string())?,
    )
    .map_err(|error| error.to_string())?;
    let base: Value = serde_json::from_str(
        &fs::read_to_string(resolver.resolve(&cases.scenario))
            .map_err(|error| error.to_string())?,
    )
    .map_err(|error| error.to_string())?;
    let result = match mode {
        "chaos-sweep" => Value::Array(
            cases
                .sweep
                .cases
                .iter()
                .map(|selected| {
                    sweep_case(&base, selected, cases.sweep.duration, cases.sweep.burn_in)
                })
                .collect::<Result<Vec<_>, _>>()?,
        ),
        "chaos-paired" => sensitivity_case(&base, &cases.sensitivity)?,
        "chaos-preflight" => sensitivity_case(&base, &cases.initialization_preflight)?,
        _ => return Err(format!("unknown chaos mode {mode}")),
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
                serde_json::to_string_pretty(&result).map_err(|error| error.to_string())?
            ),
        )
        .map_err(|error| error.to_string())?;
    }
    Ok(result)
}

#[cfg(test)]
mod tests {
    use super::{CasesWire, run_chaos, sensitivity_case};
    use crate::resources::ResourceResolver;
    use serde_json::Value;
    use std::fs;

    #[test]
    fn symmetric_preflight_realizes_the_shared_perturbation_envelope() {
        let resolver = ResourceResolver::discover(None).unwrap();
        let cases: CasesWire = serde_json::from_str(
            &fs::read_to_string(resolver.resolve("spec/conformance/chaotic-wake-cases.json"))
                .unwrap(),
        )
        .unwrap();
        let base: Value =
            serde_json::from_str(&fs::read_to_string(resolver.resolve(&cases.scenario)).unwrap())
                .unwrap();
        let result = sensitivity_case(&base, &cases.initialization_preflight).unwrap();
        let ratio = result["initialization"]["realized_to_requested_ratio"]
            .as_f64()
            .unwrap();
        assert!((0.02..=0.2).contains(&ratio));
        assert_eq!(
            result["initialization"]["reference_import_status"],
            "accepted"
        );
        assert_eq!(
            result["initialization"]["perturbed_import_status"],
            "accepted"
        );
    }

    #[test]
    fn unknown_chaos_mode_is_rejected_before_work() {
        let resolver = ResourceResolver::discover(None).unwrap();
        assert!(run_chaos(&resolver, "unknown", None).is_err());
    }
}
