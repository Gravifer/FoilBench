//! Cross-producer canonical import gate for the native Rust implementation.

use std::{
    collections::BTreeSet,
    fs,
    path::{Path, PathBuf},
};

use foilbench_core::{
    CanonicalGeometryDescriptor, ControlState, FlowSolver, LbmD2q9, NacaFoil, PicFlip, Scenario,
    StableFluids,
};
use serde_json::{Value, json};

use crate::{canonical_io::read_canonical, resources::ResourceResolver};

const SOLVERS: [&str; 3] = ["stable-fluids", "lbm-d2q9", "pic-flip"];
const PRODUCERS: [&str; 4] = ["python", "julia", "typescript", "rust"];

fn manifests(directory: &Path, output: &mut Vec<PathBuf>) -> Result<(), String> {
    for entry in fs::read_dir(directory).map_err(|error| error.to_string())? {
        let path = entry.map_err(|error| error.to_string())?.path();
        if path.is_dir() {
            if path.join("manifest.json").is_file() {
                output.push(path);
            } else {
                manifests(&path, output)?;
            }
        }
    }
    Ok(())
}

fn solver(id: &str) -> Result<Box<dyn FlowSolver<f32>>, String> {
    match id {
        "stable-fluids" => Ok(Box::new(StableFluids::<f32>::new("native"))),
        "lbm-d2q9" => Ok(Box::new(LbmD2q9::<f32>::new("native"))),
        "pic-flip" => Ok(Box::new(PicFlip::<f32>::new("native"))),
        _ => Err(format!("unsupported solver {id}")),
    }
}

/// Import every canonical snapshot into every native Rust destination family.
pub fn run_interchange(resolver: &ResourceResolver, results: &Path) -> Result<usize, String> {
    let document = fs::read_to_string(resolver.resolve("scenarios/airfoil/default.json"))
        .map_err(|error| error.to_string())?;
    let base: Value = serde_json::from_str(&document).map_err(|error| error.to_string())?;
    let foil = &base["foil"];
    let expected_geometry = CanonicalGeometryDescriptor {
        family: "naca-four-digit-v1".into(),
        naca: foil["naca"]
            .as_str()
            .ok_or("scenario foil NACA missing")?
            .into(),
        chord: foil["chord"]
            .as_f64()
            .ok_or("scenario foil chord missing")?,
        pivot: foil["pivot"]
            .as_array()
            .ok_or("scenario foil pivot missing")?
            .iter()
            .map(|value| value.as_f64().ok_or("scenario foil pivot invalid"))
            .collect::<Result<Vec<_>, _>>()?,
    };
    let mut directories = Vec::new();
    manifests(results, &mut directories)?;
    let expected = PRODUCERS
        .iter()
        .flat_map(|producer| {
            SOLVERS
                .iter()
                .map(move |solver| (producer.to_string(), solver.to_string()))
        })
        .collect::<BTreeSet<_>>();
    let mut observed = BTreeSet::new();
    let mut conversions = 0_usize;
    for directory in directories {
        let state = read_canonical::<f32>(&directory, Some(&expected_geometry))?;
        let source = (
            state.producer.implementation.clone(),
            state.source_solver.clone(),
        );
        if !observed.insert(source.clone()) {
            return Err(format!("duplicate canonical snapshot from {source:?}"));
        }
        let mut selected = base.clone();
        selected["resolution"] = json!(state.resolution);
        selected["precision"] = json!("float32");
        let scenario = Scenario::from_json(
            &serde_json::to_string(&selected).map_err(|error| error.to_string())?,
        )
        .map_err(|error| error.to_string())?;
        let geometry = NacaFoil::new(scenario.foil().clone()).map_err(|error| error.to_string())?;
        let control = ControlState {
            time: state.time,
            angle_degrees: state.angle_degrees,
            angular_velocity_degrees: state.angular_velocity_degrees,
        };
        for destination_id in SOLVERS {
            let mut destination = solver(destination_id)?;
            destination
                .initialize(&scenario, &geometry, scenario.seed())
                .map_err(|error| error.to_string())?;
            let outcome = destination.import_state(&state, control);
            if !outcome.accepted {
                return Err(format!(
                    "Rust rejected {source:?} in {destination_id}: {:?} at {:?}",
                    outcome.reason, outcome.stage,
                ));
            }
            conversions += 1;
        }
    }
    if observed != expected {
        return Err(format!(
            "canonical producer roster mismatch: missing={:?} extra={:?}",
            expected.difference(&observed).collect::<Vec<_>>(),
            observed.difference(&expected).collect::<Vec<_>>(),
        ));
    }
    Ok(conversions)
}
