import json
from pathlib import Path
from typing import cast

import numpy as np
import pytest

from foilbench_py.core.models import CanonicalFlowState, Precision
from foilbench_py.core.scenario import find_repo_root
from foilbench_py.core.state_io import load_canonical_state, save_canonical_state


@pytest.mark.parametrize("precision", ["float32", "float64"])
def test_canonical_state_round_trips_declared_axes_and_storage(
    precision: Precision,
) -> None:
    dtype = np.dtype(precision)
    velocity = np.arange(1 * 4 * 8 * 2, dtype=dtype).reshape(1, 4, 8, 2) / 31.0
    density = np.ones((1, 4, 8), dtype=dtype)
    state = CanonicalFlowState(
        schema_version=1,
        dimension=2,
        bounds=((0.0, 2.0), (-0.5, 0.5)),
        resolution=(8, 4),
        periodic_axes=("x",),
        time=1.25,
        precision=precision,
        angle_degrees=17.0,
        angular_velocity_degrees=-2.5,
        source_language="python",
        source_solver="test",
        velocity=velocity,
        density=density,
    )

    root = find_repo_root(Path(__file__))
    directory = save_canonical_state(state, root / "results" / f"test-state-io-{precision}")
    loaded = load_canonical_state(directory)
    manifest = cast(
        dict[str, object],
        json.loads((directory / "manifest.json").read_text(encoding="utf-8")),
    )

    assert manifest["velocity"] == {
        "file": "velocity.npy",
        "axes": ["z", "y", "x", "component"],
        "order": "C",
    }
    assert manifest["density"] == {
        "file": "density.npy",
        "axes": ["z", "y", "x"],
        "order": "C",
    }
    assert loaded.precision == precision
    assert loaded.periodic_axes == ("x",)
    assert loaded.time == 1.25
    np.testing.assert_array_equal(loaded.velocity, velocity)
    assert loaded.density is not None
    np.testing.assert_array_equal(loaded.density, density)


def test_canonical_state_reader_rejects_semantically_swapped_axes() -> None:
    state = CanonicalFlowState(
        schema_version=1,
        dimension=2,
        bounds=((0.0, 1.0), (0.0, 1.0)),
        resolution=(8, 4),
        periodic_axes=(),
        time=0.0,
        precision="float32",
        angle_degrees=0.0,
        angular_velocity_degrees=0.0,
        source_language="python",
        source_solver="test",
        velocity=np.zeros((1, 4, 8, 2), dtype=np.float32),
    )
    root = find_repo_root(Path(__file__))
    directory = save_canonical_state(state, root / "results" / "test-state-io-bad-axes")
    manifest_path = directory / "manifest.json"
    manifest = cast(
        dict[str, object],
        json.loads(manifest_path.read_text(encoding="utf-8")),
    )
    velocity_metadata = cast(dict[str, object], manifest["velocity"])
    velocity_metadata["axes"] = ["z", "x", "y", "component"]
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")

    with pytest.raises(ValueError, match="axes"):
        load_canonical_state(directory)
