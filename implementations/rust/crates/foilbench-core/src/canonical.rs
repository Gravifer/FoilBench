use serde::{Deserialize, Serialize};
use serde_json::Value;

use crate::{geometry::FoilDescriptor, scenario::Precision};

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
    pub geometry: FoilDescriptor,
    pub producer: Producer,
    pub source_solver: String,
    pub velocity: ArrayMetadata,
    pub density: Option<ArrayMetadata>,
}

#[derive(Clone, Debug, PartialEq)]
pub enum CanonicalManifest {
    V1(CanonicalManifestV1),
    V2(CanonicalManifestV2),
}

impl CanonicalManifest {
    /// Parse a canonical manifest and dispatch on its schema version.
    ///
    /// # Errors
    ///
    /// Returns the underlying JSON decoding error for malformed or unsupported data.
    pub fn from_json(document: &str) -> Result<Self, serde_json::Error> {
        let value: Value = serde_json::from_str(document)?;
        match value.get("schema_version").and_then(Value::as_u64) {
            Some(1) => serde_json::from_value(value).map(Self::V1),
            Some(2) => serde_json::from_value(value).map(Self::V2),
            _ => serde_json::from_value::<CanonicalManifestV1>(value).map(Self::V1),
        }
    }
}

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
}
