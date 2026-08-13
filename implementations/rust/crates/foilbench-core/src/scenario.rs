use std::collections::BTreeMap;

use serde::{Deserialize, Serialize};
use serde_json::Value;

use crate::geometry::FoilDescriptor;

#[derive(Clone, Copy, Debug, PartialEq, Serialize, Deserialize)]
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

#[derive(Clone, Debug, PartialEq, Serialize, Deserialize)]
pub struct Scenario {
    pub schema_version: u32,
    pub id: String,
    pub dimension: u8,
    pub bounds: Vec<[f64; 2]>,
    pub resolution: Vec<usize>,
    pub periodic_axes: Vec<String>,
    pub reynolds: f64,
    pub freestream: Vec<f64>,
    pub foil: FoilDescriptor,
    pub controls: Vec<ControlKeyframe>,
    pub duration: f64,
    pub output_dt: f64,
    pub precision: Precision,
    pub seed: u64,
    #[serde(default)]
    pub solver_options: BTreeMap<String, Value>,
}

impl Scenario {
    /// Validate semantic constraints that are not expressible by field types alone.
    ///
    /// # Errors
    ///
    /// Returns an error for inconsistent dimensions, controls, bounds, or physical values.
    pub fn validate(&self) -> Result<(), &'static str> {
        let dimension = usize::from(self.dimension);
        if self.schema_version != 1 || !(self.dimension == 2 || self.dimension == 3) {
            return Err("unsupported scenario schema or dimension");
        }
        if self.bounds.len() != dimension
            || self.resolution.len() != dimension
            || self.freestream.len() != dimension
            || self.foil.pivot.len() != dimension
        {
            return Err("scenario dimensions disagree");
        }
        if self.controls.is_empty()
            || self
                .controls
                .windows(2)
                .any(|pair| pair[1].time < pair[0].time)
        {
            return Err("control history must be nonempty and sorted");
        }
        if !self.reynolds.is_finite()
            || self.reynolds <= 0.0
            || !self.duration.is_finite()
            || self.duration <= 0.0
            || !self.output_dt.is_finite()
            || self.output_dt <= 0.0
        {
            return Err("scenario physical values must be finite and positive");
        }
        if self
            .bounds
            .iter()
            .any(|bound| !bound[0].is_finite() || !bound[1].is_finite() || bound[1] <= bound[0])
        {
            return Err("domain bounds must be finite and increasing");
        }
        Ok(())
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
                let duration = right.time - left.time;
                if duration <= 0.0 {
                    return ControlState {
                        time,
                        angle_degrees: right.angle_degrees,
                        angular_velocity_degrees: 0.0,
                    };
                }
                let linear = (time - left.time) / duration;
                let smooth = linear * linear * (3.0 - 2.0 * linear);
                let delta = right.angle_degrees - left.angle_degrees;
                return ControlState {
                    time,
                    angle_degrees: left.angle_degrees + smooth * delta,
                    angular_velocity_degrees: 6.0 * linear * (1.0 - linear) * delta / duration,
                };
            }
        }
        unreachable!("validated control history covers the selected time")
    }
}

#[cfg(test)]
mod tests {
    use super::Scenario;

    #[test]
    fn loads_repository_default_scenario() {
        let document = include_str!("../../../../../scenarios/airfoil/default.json");
        let scenario: Scenario = serde_json::from_str(document).unwrap();
        scenario.validate().unwrap();
        assert_eq!(scenario.dimension, 2);
        assert!((scenario.control_at(4.0).angle_degrees - 9.0).abs() < 1.0e-12);
    }
}
