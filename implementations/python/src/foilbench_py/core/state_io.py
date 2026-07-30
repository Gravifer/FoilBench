"""Portable canonical-state serialization."""

import json
from pathlib import Path

import numpy as np

from foilbench_py.core.models import CanonicalFlowState


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
