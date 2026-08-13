"""Portable canonical-state serialization."""

import json
from pathlib import Path
from typing import cast

import numpy as np
from jsonschema.exceptions import ValidationError

from foilbench_py.core._schema_adapter import validate_json
from foilbench_py.core.models import AxisName, CanonicalFlowState, FoilSpec, Precision
from foilbench_py.core.scenario import find_repo_root
from foilbench_py.types import VelocityField


def _json_object(path: Path) -> dict[str, object]:
    loaded = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(loaded, dict):
        raise TypeError(f"{path} must contain a JSON object")
    return cast(dict[str, object], loaded)


def _array_metadata(
    manifest: dict[str, object],
    name: str,
    expected_axes: list[str],
) -> dict[str, object] | None:
    raw = manifest.get(name)
    if raw is None:
        return None
    if not isinstance(raw, dict):
        raise TypeError(f"canonical {name} metadata must be an object")
    metadata = cast(dict[str, object], raw)
    if metadata.get("file") != f"{name}.npy":
        raise ValueError(f"canonical {name} must use {name}.npy")
    if metadata.get("axes") != expected_axes:
        raise ValueError(f"canonical {name} axes must be {expected_axes}")
    if metadata.get("order") not in ("C", "F"):
        raise ValueError(f"canonical {name} must declare C or Fortran order")
    return metadata


def _matches_declared_order(array: np.ndarray, metadata: dict[str, object]) -> bool:
    order = metadata["order"]
    return bool(array.flags.c_contiguous if order == "C" else array.flags.f_contiguous)


def save_canonical_state(state: CanonicalFlowState, directory: str | Path) -> Path:
    destination = Path(directory)
    destination.mkdir(parents=True, exist_ok=True)
    velocity = np.ascontiguousarray(
        np.asarray(state.velocity, dtype=state.precision).astype(
            np.dtype(state.precision).newbyteorder("<"), copy=False
        )
    )
    np.save(destination / "velocity.npy", velocity, allow_pickle=False)
    if state.density is not None:
        density = np.ascontiguousarray(
            np.asarray(state.density, dtype=state.precision).astype(
                np.dtype(state.precision).newbyteorder("<"), copy=False
            )
        )
        np.save(destination / "density.npy", density, allow_pickle=False)
    manifest: dict[str, object] = {
        "schema_version": state.schema_version,
        "dimension": state.dimension,
        "bounds": state.bounds,
        "resolution": state.resolution,
        "periodic_axes": state.periodic_axes,
        "time": state.time,
        "precision": state.precision,
        "angle_degrees": state.angle_degrees,
        "angular_velocity_degrees": state.angular_velocity_degrees,
        "source_solver": state.source_solver,
        "velocity": {
            "file": "velocity.npy",
            "axes": ["z", "y", "x", "component"],
            "order": "C",
        },
        "density": None
        if state.density is None
        else {"file": "density.npy", "axes": ["z", "y", "x"], "order": "C"},
    }
    if state.schema_version == 1:
        manifest["source_language"] = state.source_language
    else:
        if state.geometry is None or state.producer_execution_target is None:
            raise ValueError("canonical v2 requires geometry and producer target")
        manifest["geometry"] = {
            "family": "naca-four-digit-v1",
            "naca": state.geometry.naca,
            "chord": state.geometry.chord,
            "pivot": state.geometry.pivot,
        }
        manifest["producer"] = {
            "implementation": state.source_language,
            "execution_target": state.producer_execution_target,
        }
    (destination / "manifest.json").write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    return destination


def load_canonical_state(directory: str | Path) -> CanonicalFlowState:
    """Load and validate the language-neutral canonical-state directory."""
    source = Path(directory)
    manifest = _json_object(source / "manifest.json")
    root = find_repo_root(source)
    try:
        version = int(cast(int, manifest.get("schema_version", 0)))
        schema_path = (
            root / "spec" / "schemas" / "canonical-manifest.schema.json"
            if version == 1
            else root / "spec" / "proposals" / "revision5" / "schemas" / "canonical-manifest-v2.schema.json"
        )
        validate_json(manifest, _json_object(schema_path))
    except ValidationError as error:
        raise ValueError(
            f"invalid canonical manifest at {error.json_path}: {error.message}"
        ) from error
    velocity_metadata = _array_metadata(
        manifest, "velocity", ["z", "y", "x", "component"]
    )
    if velocity_metadata is None:
        raise ValueError("canonical velocity metadata is required")
    density_metadata = _array_metadata(manifest, "density", ["z", "y", "x"])

    precision_value = str(manifest["precision"])
    if precision_value not in ("float32", "float64"):
        raise ValueError("canonical precision must be float32 or float64")
    precision: Precision = precision_value
    selected_dtype = np.dtype(precision)
    velocity_file = np.load(source / "velocity.npy", allow_pickle=False)
    if velocity_file.dtype != selected_dtype or not _matches_declared_order(
        velocity_file, velocity_metadata
    ):
        raise ValueError("canonical velocity dtype/order does not match its manifest")
    velocity = np.asarray(velocity_file, dtype=selected_dtype)

    density = None
    if density_metadata is not None:
        density_file = np.load(source / "density.npy", allow_pickle=False)
        if density_file.dtype != selected_dtype or not _matches_declared_order(
            density_file, density_metadata
        ):
            raise ValueError("canonical density dtype/order does not match its manifest")
        density = np.asarray(density_file, dtype=selected_dtype)

    raw_bounds = cast(list[list[float]], manifest["bounds"])
    raw_resolution = cast(list[int], manifest["resolution"])
    raw_periodic_axes = cast(list[str], manifest["periodic_axes"])
    dimension_value = int(cast(int, manifest["dimension"]))
    if dimension_value not in (2, 3):
        raise ValueError("canonical dimension must be 2 or 3")
    version = int(cast(int, manifest["schema_version"]))
    raw_geometry = manifest.get("geometry")
    geometry = None
    if isinstance(raw_geometry, dict):
        typed_geometry = cast(dict[str, object], raw_geometry)
        geometry = FoilSpec(
            naca=str(typed_geometry["naca"]),
            chord=float(cast(float, typed_geometry["chord"])),
            pivot=tuple(float(value) for value in cast(list[float], typed_geometry["pivot"])),
        )
    raw_producer = manifest.get("producer")
    source_language = str(manifest.get("source_language", ""))
    producer_target = None
    if isinstance(raw_producer, dict):
        typed_producer = cast(dict[str, object], raw_producer)
        source_language = str(typed_producer["implementation"])
        producer_target = str(typed_producer["execution_target"])
    return CanonicalFlowState(
        schema_version=version,
        dimension=dimension_value,
        bounds=tuple((float(pair[0]), float(pair[1])) for pair in raw_bounds),
        resolution=tuple(int(value) for value in raw_resolution),
        periodic_axes=tuple(cast(AxisName, value) for value in raw_periodic_axes),
        time=float(cast(float, manifest["time"])),
        precision=precision,
        angle_degrees=float(cast(float, manifest["angle_degrees"])),
        angular_velocity_degrees=float(cast(float, manifest["angular_velocity_degrees"])),
        source_language=source_language,
        source_solver=str(manifest["source_solver"]),
        velocity=velocity,
        density=density,
        geometry=geometry,
        producer_execution_target=producer_target,
    )


def midspan_velocity(state: CanonicalFlowState) -> VelocityField:
    """Extract the canonical mid-span plane used by the default 2D presentation."""
    return state.velocity[state.velocity.shape[0] // 2]
