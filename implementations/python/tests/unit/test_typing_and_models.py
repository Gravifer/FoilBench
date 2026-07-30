# pyright: reportUnknownVariableType=false
import numpy as np
import pytest
from beartype import beartype
from jaxtyping import Float, TypeCheckError, jaxtyped

from foilbench_py.core.models import CanonicalFlowState


@jaxtyped(typechecker=beartype)
def _typed_pairwise_add(
    left: Float[np.ndarray, "point dim"],
    right: Float[np.ndarray, "point dim"],
) -> Float[np.ndarray, "point dim"]:
    return left + right


def test_runtime_shape_binding_rejects_inconsistent_axes() -> None:
    left = np.zeros((4, 2), dtype=np.float32)
    right = np.zeros((3, 2), dtype=np.float32)
    with pytest.raises(TypeCheckError):
        _typed_pairwise_add(left, right)


def test_canonical_state_rejects_wrong_shape() -> None:
    with pytest.raises((ValueError, TypeCheckError)):
        CanonicalFlowState(
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
            velocity=np.zeros((4, 8, 2), dtype=np.float32),
        )
