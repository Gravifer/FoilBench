use std::{collections::BTreeSet, fmt};

use serde::{Deserialize, Serialize};
use serde_json::Value;

use crate::scenario::Precision;

#[derive(Clone, Debug, PartialEq, Serialize, Deserialize)]
pub struct ArrayMetadata {
    pub file: String,
    pub axes: Vec<String>,
    pub order: String,
}

#[derive(Clone, Debug, PartialEq, Serialize, Deserialize)]
pub struct Producer {
    pub implementation: String,
    pub execution_target: String,
    #[serde(default)]
    pub build: Option<Value>,
}

#[derive(Clone, Debug, PartialEq, Serialize, Deserialize)]
pub struct CanonicalGeometryDescriptor {
    pub family: String,
    pub naca: String,
    pub chord: f64,
    pub pivot: Vec<f64>,
}

#[derive(Clone, Debug, PartialEq, Serialize, Deserialize)]
pub struct CanonicalManifestV1 {
    pub schema_version: u32,
    pub dimension: u8,
    pub bounds: Vec<[f64; 2]>,
    pub resolution: Vec<usize>,
    pub periodic_axes: Vec<String>,
    pub time: f64,
    pub precision: Precision,
    pub angle_degrees: f64,
    pub angular_velocity_degrees: f64,
    pub source_language: String,
    pub source_solver: String,
    pub velocity: ArrayMetadata,
    pub density: Option<ArrayMetadata>,
}

#[derive(Clone, Debug, PartialEq, Serialize, Deserialize)]
pub struct CanonicalManifestV2 {
    pub schema_version: u32,
    pub dimension: u8,
    pub bounds: Vec<[f64; 2]>,
    pub resolution: Vec<usize>,
    pub periodic_axes: Vec<String>,
    pub time: f64,
    pub precision: Precision,
    pub angle_degrees: f64,
    pub angular_velocity_degrees: f64,
    pub geometry: CanonicalGeometryDescriptor,
    pub producer: Producer,
    pub source_solver: String,
    pub velocity: ArrayMetadata,
    pub density: Option<ArrayMetadata>,
}

#[derive(Clone, Debug, PartialEq)]
pub enum CanonicalManifest {
    V1(Box<CanonicalManifestV1>),
    V2(Box<CanonicalManifestV2>),
}

impl CanonicalManifest {
    /// Parse and semantically validate a version 1 or version 2 manifest.
    ///
    /// # Errors
    ///
    /// Returns a classified error for malformed JSON, absent/unsupported
    /// versions, or metadata that violates canonical invariants.
    pub fn from_json(document: &str) -> Result<Self, CanonicalManifestError> {
        let value: Value = serde_json::from_str(document).map_err(CanonicalManifestError::Json)?;
        let version = value
            .get("schema_version")
            .and_then(Value::as_u64)
            .ok_or(CanonicalManifestError::MissingVersion)?;
        let manifest = match version {
            1 => Self::V1(Box::new(
                serde_json::from_value(value).map_err(CanonicalManifestError::Json)?,
            )),
            2 => Self::V2(Box::new(
                serde_json::from_value(value).map_err(CanonicalManifestError::Json)?,
            )),
            unsupported => return Err(CanonicalManifestError::UnsupportedVersion(unsupported)),
        };
        manifest.validate()?;
        Ok(manifest)
    }

    /// Validate version-specific identity and common canonical metadata.
    ///
    /// # Errors
    ///
    /// Returns a semantic error when any invariant is violated.
    pub fn validate(&self) -> Result<(), CanonicalManifestError> {
        match self {
            Self::V1(manifest) => validate_common(
                manifest.schema_version,
                1,
                manifest.dimension,
                &manifest.bounds,
                &manifest.resolution,
                &manifest.periodic_axes,
                manifest.time,
                manifest.angle_degrees,
                manifest.angular_velocity_degrees,
                &manifest.velocity,
                manifest.density.as_ref(),
            ),
            Self::V2(manifest) => {
                validate_common(
                    manifest.schema_version,
                    2,
                    manifest.dimension,
                    &manifest.bounds,
                    &manifest.resolution,
                    &manifest.periodic_axes,
                    manifest.time,
                    manifest.angle_degrees,
                    manifest.angular_velocity_degrees,
                    &manifest.velocity,
                    manifest.density.as_ref(),
                )?;
                let geometry = &manifest.geometry;
                if geometry.family != "naca-four-digit-v1"
                    || geometry.naca.len() != 4
                    || !geometry.naca.bytes().all(|digit| digit.is_ascii_digit())
                    || !geometry.chord.is_finite()
                    || geometry.chord <= 0.0
                    || geometry.pivot.len() != usize::from(manifest.dimension)
                    || geometry.pivot.iter().any(|value| !value.is_finite())
                    || manifest.producer.implementation.is_empty()
                    || manifest.producer.execution_target.is_empty()
                {
                    return Err(CanonicalManifestError::Semantic(
                        "invalid canonical v2 identity",
                    ));
                }
                Ok(())
            }
        }
    }
}

#[allow(clippy::too_many_arguments)]
fn validate_common(
    actual_version: u32,
    expected_version: u32,
    dimension: u8,
    bounds: &[[f64; 2]],
    resolution: &[usize],
    periodic_axes: &[String],
    time: f64,
    angle: f64,
    angular_velocity: f64,
    velocity: &ArrayMetadata,
    density: Option<&ArrayMetadata>,
) -> Result<(), CanonicalManifestError> {
    let dimensions = usize::from(dimension);
    let legal_axes: &[&str] = if dimension == 2 {
        &["x", "y"]
    } else {
        &["x", "y", "z"]
    };
    if actual_version != expected_version
        || !(dimension == 2 || dimension == 3)
        || bounds.len() != dimensions
        || resolution.len() != dimensions
        || resolution.contains(&0)
        || bounds
            .iter()
            .any(|bound| !bound[0].is_finite() || !bound[1].is_finite() || bound[1] <= bound[0])
        || periodic_axes
            .iter()
            .any(|axis| !legal_axes.contains(&axis.as_str()))
        || periodic_axes.iter().collect::<BTreeSet<_>>().len() != periodic_axes.len()
        || !time.is_finite()
        || time < 0.0
        || !angle.is_finite()
        || !angular_velocity.is_finite()
    {
        return Err(CanonicalManifestError::Semantic(
            "invalid canonical metadata",
        ));
    }
    validate_array(velocity, &["z", "y", "x", "component"])?;
    if let Some(metadata) = density {
        validate_array(metadata, &["z", "y", "x"])?;
    }
    Ok(())
}

fn validate_array(metadata: &ArrayMetadata, axes: &[&str]) -> Result<(), CanonicalManifestError> {
    if metadata.file.is_empty()
        || metadata
            .axes
            .iter()
            .map(String::as_str)
            .ne(axes.iter().copied())
        || !matches!(metadata.order.as_str(), "C" | "F")
    {
        return Err(CanonicalManifestError::Semantic(
            "invalid canonical array metadata",
        ));
    }
    Ok(())
}

#[derive(Debug)]
pub enum CanonicalManifestError {
    Json(serde_json::Error),
    MissingVersion,
    UnsupportedVersion(u64),
    Semantic(&'static str),
}

impl fmt::Display for CanonicalManifestError {
    fn fmt(&self, formatter: &mut fmt::Formatter<'_>) -> fmt::Result {
        match self {
            Self::Json(error) => error.fmt(formatter),
            Self::MissingVersion => formatter.write_str("canonical manifest lacks schema_version"),
            Self::UnsupportedVersion(version) => {
                write!(formatter, "unsupported canonical schema version {version}")
            }
            Self::Semantic(message) => formatter.write_str(message),
        }
    }
}

impl std::error::Error for CanonicalManifestError {}

#[cfg(test)]
mod tests {
    use super::CanonicalManifest;

    #[test]
    fn reads_revision_four_manifest() {
        let document =
            include_str!("../../../../../spec/conformance/canonical-state-f32/manifest.json");
        assert!(matches!(
            CanonicalManifest::from_json(document).unwrap(),
            CanonicalManifest::V1(_)
        ));
    }

    #[test]
    fn rejects_unknown_or_missing_versions() {
        let document =
            include_str!("../../../../../spec/conformance/canonical-state-f32/manifest.json");
        assert!(
            CanonicalManifest::from_json(
                &document.replace("\"schema_version\": 1", "\"schema_version\": 3")
            )
            .is_err()
        );
        assert!(
            CanonicalManifest::from_json(&document.replace("\"schema_version\": 1,", "")).is_err()
        );
    }

    #[test]
    fn requires_complete_v2_geometry_identity() {
        let document = r#"{
          "schema_version":2,"dimension":2,"bounds":[[-1,1],[-1,1]],
          "resolution":[4,4],"periodic_axes":[],"time":0,"precision":"float32",
          "angle_degrees":0,"angular_velocity_degrees":0,
          "geometry":{"naca":"0012","chord":1,"pivot":[0,0]},
          "producer":{"implementation":"rust","execution_target":"native"},
          "source_solver":"stable-fluids",
          "velocity":{"file":"velocity.npy","axes":["z","y","x","component"],"order":"C"},
          "density":null
        }"#;
        assert!(CanonicalManifest::from_json(document).is_err());
    }

    #[test]
    fn consumes_revision5_manifest_fixture() {
        let document = include_str!(
            "../../../../../spec/proposals/revision5/fixtures/canonical-manifest-v2.json"
        );
        assert!(matches!(
            CanonicalManifest::from_json(document).unwrap(),
            CanonicalManifest::V2(_)
        ));
    }
}
