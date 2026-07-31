"""Portable canonical-state serialization."""

import json
from pathlib import Path
from typing import cast

import numpy as np

from foilbench_py.core.models import AxisName, CanonicalFlowState, Precision


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
    if metadata.get("order") != "C":
        raise ValueError(f"canonical {name} must declare C order")
    return metadata


def save_canonical_state(state: CanonicalFlowState, directory: str | Path) -> Path:
    destination = Path(directory)
    destination.mkdir(parents=True, exist_ok=True)
    velocity = np.asarray(state.velocity, dtype=state.precision).astype(
        np.dtype(state.precision).newbyteorder("<"), copy=False
    )
    np.save(destination / "velocity.npy", velocity, allow_pickle=False)
    if state.density is not None:
        density = np.asarray(state.density, dtype=state.precision).astype(
            np.dtype(state.precision).newbyteorder("<"), copy=False
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
        "source_language": state.source_language,
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
    (destination / "manifest.json").write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    return destination


def load_canonical_state(directory: str | Path) -> CanonicalFlowState:
    """Load and validate the language-neutral canonical-state directory."""
    source = Path(directory)
    manifest = _json_object(source / "manifest.json")
    _array_metadata(manifest, "velocity", ["z", "y", "x", "component"])
    density_metadata = _array_metadata(manifest, "density", ["z", "y", "x"])

    precision_value = str(manifest["precision"])
    if precision_value not in ("float32", "float64"):
        raise ValueError("canonical precision must be float32 or float64")
    precision: Precision = precision_value
    selected_dtype = np.dtype(precision)
    velocity_file = np.load(source / "velocity.npy", allow_pickle=False)
    if velocity_file.dtype != selected_dtype or not velocity_file.flags.c_contiguous:
        raise ValueError("canonical velocity dtype/order does not match its manifest")
    velocity = np.asarray(velocity_file, dtype=selected_dtype)

    density = None
    if density_metadata is not None:
        density_file = np.load(source / "density.npy", allow_pickle=False)
        if density_file.dtype != selected_dtype or not density_file.flags.c_contiguous:
            raise ValueError("canonical density dtype/order does not match its manifest")
        density = np.asarray(density_file, dtype=selected_dtype)

    raw_bounds = cast(list[list[float]], manifest["bounds"])
    raw_resolution = cast(list[int], manifest["resolution"])
    raw_periodic_axes = cast(list[str], manifest["periodic_axes"])
    dimension_value = int(cast(int, manifest["dimension"]))
    if dimension_value not in (2, 3):
        raise ValueError("canonical dimension must be 2 or 3")
    return CanonicalFlowState(
        schema_version=int(cast(int, manifest["schema_version"])),
        dimension=dimension_value,
        bounds=tuple((float(pair[0]), float(pair[1])) for pair in raw_bounds),
        resolution=tuple(int(value) for value in raw_resolution),
        periodic_axes=tuple(cast(AxisName, value) for value in raw_periodic_axes),
        time=float(cast(float, manifest["time"])),
        precision=precision,
        angle_degrees=float(cast(float, manifest["angle_degrees"])),
        angular_velocity_degrees=float(cast(float, manifest["angular_velocity_degrees"])),
        source_language=str(manifest["source_language"]),
        source_solver=str(manifest["source_solver"]),
        velocity=velocity,
        density=density,
    )
