# pyright: reportUnknownVariableType=false
import numpy as np
import pytest
from beartype import beartype
from jaxtyping import Float, TypeCheckError, jaxtyped

from foilbench_py.core.interpolation import sample_vector
from foilbench_py.core.models import CanonicalFlowState, DomainSpec


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


def test_canonical_state_rejects_nonfinite_density() -> None:
    density = np.ones((1, 4, 8), dtype=np.float32)
    density[0, 2, 3] = np.nan
    with pytest.raises(ValueError, match="density contains non-finite"):
        CanonicalFlowState(
            schema_version=1,
            dimension=2,
            bounds=((0.0, 2.0), (0.0, 1.0)),
            resolution=(8, 4),
            periodic_axes=(),
            time=0.0,
            precision="float32",
            angle_degrees=0.0,
            angular_velocity_degrees=0.0,
            source_language="python",
            source_solver="test",
            velocity=np.zeros((1, 4, 8, 2), dtype=np.float32),
            density=density,
        )


def test_sampling_rejects_swapped_axes_wrong_rank_and_dimension() -> None:
    domain = DomainSpec(2, ((0.0, 2.0), (0.0, 1.0)), (8, 4))
    points = np.zeros((3, 2), dtype=np.float32)
    with pytest.raises((ValueError, TypeCheckError)):
        sample_vector(np.zeros((8, 4, 2), dtype=np.float32), points, domain)
    with pytest.raises((ValueError, TypeCheckError)):
        sample_vector(np.zeros((4, 8), dtype=np.float32), points, domain)
    with pytest.raises((ValueError, TypeCheckError)):
        sample_vector(np.zeros((4, 8, 3), dtype=np.float32), points, domain)


def test_sampling_and_canonical_state_reject_incorrect_dtypes() -> None:
    domain = DomainSpec(2, ((0.0, 2.0), (0.0, 1.0)), (8, 4))
    with pytest.raises((TypeError, TypeCheckError)):
        sample_vector(
            np.zeros((4, 8, 2), dtype=np.int32),
            np.zeros((3, 2), dtype=np.int32),
            domain,
        )
    with pytest.raises((TypeError, TypeCheckError)):
        CanonicalFlowState(
            schema_version=1,
            dimension=2,
            bounds=((0.0, 2.0), (0.0, 1.0)),
            resolution=(8, 4),
            periodic_axes=(),
            time=0.0,
            precision="float32",
            angle_degrees=0.0,
            angular_velocity_degrees=0.0,
            source_language="python",
            source_solver="test",
            velocity=np.zeros((1, 4, 8, 2), dtype=np.float64),
        )
