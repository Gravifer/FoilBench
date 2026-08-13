//! Canonical state directory I/O for native Rust.

use std::{fs, path::Path};

use foilbench_core::canonical::ArrayMetadata;
use foilbench_core::{
    CanonicalFlowState2, CanonicalGeometryDescriptor, CanonicalManifest, CanonicalManifestV2,
    FlowScalar, Precision, Producer,
};

use crate::npy::{read_npy, write_npy};

/// Write canonical version 2 and little-endian NPY payloads.
///
/// # Errors
///
/// Returns an error for malformed state data or filesystem failures.
pub fn write_canonical<T: FlowScalar>(
    directory: &Path,
    state: &CanonicalFlowState2<T>,
) -> Result<(), String> {
    state
        .validate_payload()
        .map_err(|error| error.to_string())?;
    fs::create_dir_all(directory).map_err(|error| error.to_string())?;
    let [nx, ny] = state.resolution;
    let velocity = state
        .velocity
        .iter()
        .flat_map(|value| value.iter().copied())
        .collect::<Vec<_>>();
    write_npy(&directory.join("velocity.npy"), &[1, ny, nx, 2], &velocity)?;
    if let Some(density) = &state.density {
        write_npy(&directory.join("density.npy"), &[1, ny, nx], density)?;
    }
    let manifest = CanonicalManifestV2 {
        schema_version: 2,
        dimension: 2,
        bounds: state.bounds.to_vec(),
        resolution: state.resolution.to_vec(),
        periodic_axes: state.periodic_axes.clone(),
        time: state.time,
        precision: T::PRECISION,
        angle_degrees: state.angle_degrees,
        angular_velocity_degrees: state.angular_velocity_degrees,
        geometry: state.geometry.clone(),
        producer: state.producer.clone(),
        source_solver: state.source_solver.clone(),
        velocity: ArrayMetadata {
            file: "velocity.npy".into(),
            axes: vec!["z".into(), "y".into(), "x".into(), "component".into()],
            order: "C".into(),
        },
        density: state.density.as_ref().map(|_| ArrayMetadata {
            file: "density.npy".into(),
            axes: vec!["z".into(), "y".into(), "x".into()],
            order: "C".into(),
        }),
    };
    let document = serde_json::to_string_pretty(&manifest).map_err(|error| error.to_string())?;
    fs::write(directory.join("manifest.json"), format!("{document}\n"))
        .map_err(|error| error.to_string())
}

struct CommonManifest<'a> {
    bounds: &'a [[f64; 2]],
    resolution: &'a [usize],
    periodic_axes: &'a [String],
    time: f64,
    precision: Precision,
    angle: f64,
    angular_velocity: f64,
    geometry: CanonicalGeometryDescriptor,
    producer: Producer,
    source_solver: &'a str,
    velocity: &'a ArrayMetadata,
    density: Option<&'a ArrayMetadata>,
}

/// Read canonical v1/v2. Version 1 requires caller-supplied expected geometry.
///
/// # Errors
///
/// Returns an error for invalid metadata, identity, array layout, dtype, shape,
/// or payload. Version 1 rejects when `expected_v1_geometry` is absent.
pub fn read_canonical<T: FlowScalar>(
    directory: &Path,
    expected_v1_geometry: Option<&CanonicalGeometryDescriptor>,
) -> Result<CanonicalFlowState2<T>, String> {
    let document =
        fs::read_to_string(directory.join("manifest.json")).map_err(|error| error.to_string())?;
    let manifest = CanonicalManifest::from_json(&document).map_err(|error| error.to_string())?;
    let common = match &manifest {
        CanonicalManifest::V1(selected) => CommonManifest {
            bounds: &selected.bounds,
            resolution: &selected.resolution,
            periodic_axes: &selected.periodic_axes,
            time: selected.time,
            precision: selected.precision,
            angle: selected.angle_degrees,
            angular_velocity: selected.angular_velocity_degrees,
            geometry: expected_v1_geometry
                .cloned()
                .ok_or("canonical v1 reading requires expected geometry")?,
            producer: Producer {
                implementation: selected.source_language.clone(),
                execution_target: "native".into(),
                build: None,
            },
            source_solver: &selected.source_solver,
            velocity: &selected.velocity,
            density: selected.density.as_ref(),
        },
        CanonicalManifest::V2(selected) => CommonManifest {
            bounds: &selected.bounds,
            resolution: &selected.resolution,
            periodic_axes: &selected.periodic_axes,
            time: selected.time,
            precision: selected.precision,
            angle: selected.angle_degrees,
            angular_velocity: selected.angular_velocity_degrees,
            geometry: selected.geometry.clone(),
            producer: selected.producer.clone(),
            source_solver: &selected.source_solver,
            velocity: &selected.velocity,
            density: selected.density.as_ref(),
        },
    };
    if common.bounds.len() != 2 || common.resolution.len() != 2 || common.precision != T::PRECISION
    {
        return Err(
            "canonical state dimension or precision differs from requested Rust type".into(),
        );
    }
    if common.velocity.file != "velocity.npy" {
        return Err("canonical velocity filename must be velocity.npy".into());
    }
    let velocity_npy = read_npy(&directory.join(&common.velocity.file))?;
    let nx = common.resolution[0];
    let ny = common.resolution[1];
    if velocity_npy.precision != common.precision
        || velocity_npy.shape != [1, ny, nx, 2]
        || common.velocity.order != if velocity_npy.fortran_order { "F" } else { "C" }
    {
        return Err("canonical velocity NPY metadata disagrees with manifest".into());
    }
    let velocity = velocity_npy
        .values
        .chunks_exact(2)
        .map(|value| [T::from_f64(value[0]), T::from_f64(value[1])])
        .collect::<Vec<_>>();
    let density = if let Some(metadata) = common.density {
        if metadata.file != "density.npy" {
            return Err("canonical density filename must be density.npy".into());
        }
        let selected = read_npy(&directory.join(&metadata.file))?;
        if selected.precision != common.precision
            || selected.shape != [1, ny, nx]
            || metadata.order != if selected.fortran_order { "F" } else { "C" }
        {
            return Err("canonical density NPY metadata disagrees with manifest".into());
        }
        Some(selected.values.into_iter().map(T::from_f64).collect())
    } else {
        None
    };
    let state = CanonicalFlowState2 {
        bounds: [common.bounds[0], common.bounds[1]],
        resolution: [nx, ny],
        periodic_axes: common.periodic_axes.to_vec(),
        time: common.time,
        angle_degrees: common.angle,
        angular_velocity_degrees: common.angular_velocity,
        geometry: common.geometry,
        producer: common.producer,
        source_solver: common.source_solver.into(),
        velocity,
        density,
    };
    state
        .validate_payload()
        .map_err(|error| error.to_string())?;
    Ok(state)
}

#[cfg(test)]
mod tests {
    use super::{read_canonical, write_canonical};
    use foilbench_core::{CanonicalFlowState2, CanonicalGeometryDescriptor, Producer};

    #[test]
    fn writes_v2_and_reads_it_back() {
        let directory =
            std::env::temp_dir().join(format!("foilbench-canonical-{}", std::process::id()));
        let state = CanonicalFlowState2 {
            bounds: [[0.0, 2.0], [-1.0, 1.0]],
            resolution: [4, 3],
            periodic_axes: vec!["x".into()],
            time: 0.5,
            angle_degrees: 4.0,
            angular_velocity_degrees: 0.0,
            geometry: CanonicalGeometryDescriptor {
                family: "naca-four-digit-v1".into(),
                naca: "0012".into(),
                chord: 1.0,
                pivot: vec![0.0, 0.0],
            },
            producer: Producer {
                implementation: "rust".into(),
                execution_target: "native".into(),
                build: None,
            },
            source_solver: "stable-fluids".into(),
            velocity: vec![[1.0_f32, 0.0]; 12],
            density: None,
        };
        write_canonical(&directory, &state).unwrap();
        assert_eq!(read_canonical::<f32>(&directory, None).unwrap(), state);
        std::fs::remove_dir_all(directory).unwrap();
    }
}
