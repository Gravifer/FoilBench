use std::collections::{BTreeMap, BTreeSet};

use serde::{Deserialize, Serialize};
use serde_json::Value;

use crate::geometry::{FoilDescriptor, NacaFoil};

#[derive(Clone, Copy, Debug, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "lowercase")]
pub enum Precision {
    Float32,
    Float64,
}

#[derive(Clone, Debug, PartialEq, Serialize, Deserialize)]
pub struct ControlKeyframe {
    pub time: f64,
    pub angle_degrees: f64,
}

#[derive(Clone, Copy, Debug, PartialEq)]
pub struct ControlState {
    pub time: f64,
    pub angle_degrees: f64,
    pub angular_velocity_degrees: f64,
}

#[derive(Clone, Debug, PartialEq, Deserialize)]
struct RawScenario {
    schema_version: u32,
    id: String,
    dimension: u8,
    bounds: Vec<[f64; 2]>,
    resolution: Vec<usize>,
    periodic_axes: Vec<String>,
    reynolds: f64,
    freestream: Vec<f64>,
    foil: FoilDescriptor,
    controls: Vec<ControlKeyframe>,
    duration: f64,
    output_dt: f64,
    precision: Precision,
    seed: u32,
    #[serde(default)]
    solver_options: BTreeMap<String, Value>,
}

#[derive(Clone, Debug, PartialEq)]
pub struct Scenario {
    id: String,
    dimension: u8,
    bounds: Vec<[f64; 2]>,
    resolution: Vec<usize>,
    periodic_axes: Vec<String>,
    reynolds: f64,
    freestream: Vec<f64>,
    foil: FoilDescriptor,
    controls: Vec<ControlKeyframe>,
    duration: f64,
    output_dt: f64,
    precision: Precision,
    seed: u32,
    solver_options: BTreeMap<String, Value>,
}

impl Scenario {
    /// Parse raw JSON into a validated, immutable numerical scenario.
    ///
    /// # Errors
    ///
    /// Returns a JSON or semantic validation error without publishing a
    /// partially validated scenario.
    pub fn from_json(document: &str) -> Result<Self, ScenarioError> {
        let raw: RawScenario = serde_json::from_str(document).map_err(ScenarioError::Json)?;
        Self::try_from(raw)
    }

    #[must_use]
    pub fn id(&self) -> &str {
        &self.id
    }
    #[must_use]
    pub fn dimension(&self) -> u8 {
        self.dimension
    }
    #[must_use]
    pub fn bounds(&self) -> &[[f64; 2]] {
        &self.bounds
    }
    #[must_use]
    pub fn resolution(&self) -> &[usize] {
        &self.resolution
    }
    #[must_use]
    pub fn periodic_axes(&self) -> &[String] {
        &self.periodic_axes
    }
    #[must_use]
    pub fn reynolds(&self) -> f64 {
        self.reynolds
    }
    #[must_use]
    pub fn freestream(&self) -> &[f64] {
        &self.freestream
    }
    #[must_use]
    pub fn foil(&self) -> &FoilDescriptor {
        &self.foil
    }
    #[must_use]
    pub fn controls(&self) -> &[ControlKeyframe] {
        &self.controls
    }
    #[must_use]
    pub fn duration(&self) -> f64 {
        self.duration
    }
    #[must_use]
    pub fn output_dt(&self) -> f64 {
        self.output_dt
    }
    #[must_use]
    pub fn precision(&self) -> Precision {
        self.precision
    }
    #[must_use]
    pub fn seed(&self) -> u32 {
        self.seed
    }
    #[must_use]
    pub fn solver_options(&self) -> &BTreeMap<String, Value> {
        &self.solver_options
    }

    #[must_use]
    pub fn control_at(&self, time: f64) -> ControlState {
        let first = &self.controls[0];
        if self.controls.len() == 1 || time <= first.time {
            return ControlState {
                time,
                angle_degrees: first.angle_degrees,
                angular_velocity_degrees: 0.0,
            };
        }
        let last = &self.controls[self.controls.len() - 1];
        if time >= last.time {
            return ControlState {
                time,
                angle_degrees: last.angle_degrees,
                angular_velocity_degrees: 0.0,
            };
        }
        for pair in self.controls.windows(2) {
            let (left, right) = (&pair[0], &pair[1]);
            if time >= left.time && time <= right.time {
                let interval = right.time - left.time;
                if interval <= 0.0 {
                    return ControlState {
                        time,
                        angle_degrees: right.angle_degrees,
                        angular_velocity_degrees: 0.0,
                    };
                }
                let linear = (time - left.time) / interval;
                let smooth = linear * linear * (3.0 - 2.0 * linear);
                let delta = right.angle_degrees - left.angle_degrees;
                return ControlState {
                    time,
                    angle_degrees: left.angle_degrees + smooth * delta,
                    angular_velocity_degrees: 6.0 * linear * (1.0 - linear) * delta / interval,
                };
            }
        }
        unreachable!("validated control history covers every finite selected time")
    }
}

impl TryFrom<RawScenario> for Scenario {
    type Error = ScenarioError;

    fn try_from(raw: RawScenario) -> Result<Self, Self::Error> {
        let dimension = usize::from(raw.dimension);
        if raw.schema_version != 1 || !(raw.dimension == 2 || raw.dimension == 3) {
            return Err(ScenarioError::Semantic(
                "unsupported scenario schema or dimension",
            ));
        }
        if raw.id.is_empty()
            || raw.bounds.len() != dimension
            || raw.resolution.len() != dimension
            || raw.freestream.len() != dimension
            || raw.foil.pivot.len() != dimension
        {
            return Err(ScenarioError::Semantic(
                "scenario dimensions or identifier disagree",
            ));
        }
        if raw.resolution.iter().any(|&size| size < 4)
            || raw
                .bounds
                .iter()
                .any(|bound| !bound[0].is_finite() || !bound[1].is_finite() || bound[1] <= bound[0])
            || raw.freestream.iter().any(|value| !value.is_finite())
        {
            return Err(ScenarioError::Semantic("domain values are invalid"));
        }
        let legal_axes: &[&str] = if raw.dimension == 2 {
            &["x", "y"]
        } else {
            &["x", "y", "z"]
        };
        let unique_axes: BTreeSet<&str> = raw.periodic_axes.iter().map(String::as_str).collect();
        if unique_axes.len() != raw.periodic_axes.len()
            || unique_axes.iter().any(|axis| !legal_axes.contains(axis))
        {
            return Err(ScenarioError::Semantic("periodic axes are invalid"));
        }
        if raw.controls.is_empty()
            || raw.controls.iter().any(|control| {
                !control.time.is_finite()
                    || control.time < 0.0
                    || !control.angle_degrees.is_finite()
                    || !(-90.0..=90.0).contains(&control.angle_degrees)
            })
            || raw
                .controls
                .windows(2)
                .any(|pair| pair[1].time <= pair[0].time)
        {
            return Err(ScenarioError::Semantic("control history is invalid"));
        }
        if !raw.reynolds.is_finite()
            || raw.reynolds <= 0.0
            || !raw.duration.is_finite()
            || raw.duration <= 0.0
            || !raw.output_dt.is_finite()
            || raw.output_dt <= 0.0
        {
            return Err(ScenarioError::Semantic(
                "scenario physical values are invalid",
            ));
        }
        NacaFoil::new(raw.foil.clone()).map_err(ScenarioError::Semantic)?;
        let initial_condition = raw
            .solver_options
            .get("initial_condition")
            .and_then(serde_json::Value::as_str)
            .unwrap_or("freestream");
        let periodic: BTreeSet<&str> = raw.periodic_axes.iter().map(String::as_str).collect();
        if initial_condition == "taylor-green"
            && (raw.dimension != 2 || periodic != BTreeSet::from(["x", "y"]))
        {
            return Err(ScenarioError::Semantic(
                "Taylor-Green requires a 2D domain periodic in x and y",
            ));
        }
        if initial_condition == "poiseuille"
            && (raw.dimension != 2 || !periodic.contains("x") || periodic.contains("y"))
        {
            return Err(ScenarioError::Semantic(
                "Poiseuille requires a 2D domain periodic in x and nonperiodic in y",
            ));
        }
        Ok(Self {
            id: raw.id,
            dimension: raw.dimension,
            bounds: raw.bounds,
            resolution: raw.resolution,
            periodic_axes: raw.periodic_axes,
            reynolds: raw.reynolds,
            freestream: raw.freestream,
            foil: raw.foil,
            controls: raw.controls,
            duration: raw.duration,
            output_dt: raw.output_dt,
            precision: raw.precision,
            seed: raw.seed,
            solver_options: raw.solver_options,
        })
    }
}

#[derive(Debug)]
pub enum ScenarioError {
    Json(serde_json::Error),
    Semantic(&'static str),
}

impl std::fmt::Display for ScenarioError {
    fn fmt(&self, formatter: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        match self {
            Self::Json(error) => error.fmt(formatter),
            Self::Semantic(message) => formatter.write_str(message),
        }
    }
}

impl std::error::Error for ScenarioError {}

#[cfg(test)]
mod tests {
    use super::Scenario;
    use serde_json::Value;

    fn set_fixture_path(document: &mut Value, path: &[Value], value: Value) {
        let mut cursor = document;
        for component in &path[..path.len() - 1] {
            cursor = match component {
                Value::String(name) => cursor.get_mut(name).unwrap(),
                Value::Number(index) => cursor
                    .get_mut(usize::try_from(index.as_u64().unwrap()).unwrap())
                    .unwrap(),
                _ => panic!("invalid fixture path component"),
            };
        }
        match path.last().unwrap() {
            Value::String(name) => cursor[name] = value,
            Value::Number(index) => {
                cursor[usize::try_from(index.as_u64().unwrap()).unwrap()] = value;
            }
            _ => panic!("invalid fixture path component"),
        }
    }

    #[test]
    fn loads_repository_default_scenario() {
        let document = include_str!("../../../../../scenarios/airfoil/default.json");
        let scenario = Scenario::from_json(document).unwrap();
        assert_eq!(scenario.dimension(), 2);
        assert!((scenario.control_at(4.0).angle_degrees - 9.0).abs() < 1.0e-12);
    }

    #[test]
    fn rejects_nonfinite_controls_before_publication() {
        let document = include_str!("../../../../../scenarios/airfoil/default.json");
        let malformed = document.replacen("\"angle_degrees\": 4.0", "\"angle_degrees\": 1e999", 1);
        assert!(Scenario::from_json(&malformed).is_err());
    }

    #[test]
    fn rejects_duplicate_periodic_axes() {
        let document = include_str!("../../../../../scenarios/validation/uniform.json");
        let malformed = document.replace("[\"x\", \"y\"]", "[\"x\", \"x\"]");
        assert!(Scenario::from_json(&malformed).is_err());
    }

    #[test]
    fn consumes_revision5_fidelity_inventory() {
        let fixture: Value = serde_json::from_str(include_str!(
            "../../../../../spec/proposals/revision5/fixtures/fidelity-cases.json"
        ))
        .unwrap();
        for case in fixture["cases"].as_array().unwrap() {
            let source = match case["scenario"].as_str().unwrap() {
                "scenarios/validation/uniform.json" => {
                    include_str!("../../../../../scenarios/validation/uniform.json")
                }
                "scenarios/validation/taylor-green.json" => {
                    include_str!("../../../../../scenarios/validation/taylor-green.json")
                }
                "scenarios/validation/poiseuille.json" => {
                    include_str!("../../../../../scenarios/validation/poiseuille.json")
                }
                "scenarios/validation/naca0012-zero.json" => {
                    include_str!("../../../../../scenarios/validation/naca0012-zero.json")
                }
                "scenarios/airfoil/default.json" => {
                    include_str!("../../../../../scenarios/airfoil/default.json")
                }
                path => panic!("unrecognized fidelity scenario {path}"),
            };
            let scenario = Scenario::from_json(source).unwrap();
            assert_eq!(
                case["resolution"].as_array().unwrap().len(),
                usize::from(scenario.dimension())
            );
            assert!(
                case["metrics"]
                    .as_object()
                    .is_some_and(|value| !value.is_empty())
            );
        }
    }

    #[test]
    fn rejects_revision5_negative_scenario_fixture() {
        let fixture: Value = serde_json::from_str(include_str!(
            "../../../../../spec/proposals/revision5/fixtures/scenario-negative.json"
        ))
        .unwrap();
        for case in fixture["cases"].as_array().unwrap() {
            let base = match case["base"].as_str().unwrap() {
                "scenarios/validation/uniform.json" => {
                    include_str!("../../../../../scenarios/validation/uniform.json")
                }
                "scenarios/validation/taylor-green.json" => {
                    include_str!("../../../../../scenarios/validation/taylor-green.json")
                }
                "scenarios/validation/poiseuille.json" => {
                    include_str!("../../../../../scenarios/validation/poiseuille.json")
                }
                path => panic!("unrecognized negative-fixture scenario {path}"),
            };
            let mut document: Value = serde_json::from_str(base).unwrap();
            set_fixture_path(
                &mut document,
                case["path"].as_array().unwrap(),
                case["value"].clone(),
            );
            assert!(
                Scenario::from_json(&serde_json::to_string(&document).unwrap()).is_err(),
                "negative case {} was accepted",
                case["id"]
            );
        }
    }
}
