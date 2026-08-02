import json
from pathlib import Path
from typing import cast

import numpy as np

from foilbench_py.core.geometry import NacaFoil
from foilbench_py.core.models import FoilSpec
from foilbench_py.core.rng import PCG32
from foilbench_py.core.scenario import find_repo_root
from foilbench_py.core.state_io import load_canonical_state


def _fixture(name: str) -> Path:
    return find_repo_root(Path(__file__)) / "spec" / "conformance" / name


def test_shared_pcg32_vectors_match_python_reference() -> None:
    document = cast(
        dict[str, object],
        json.loads(_fixture("pcg32.json").read_text(encoding="utf-8")),
    )
    cases = cast(list[dict[str, object]], document["cases"])
    for case in cases:
        seed = int(cast(int, case["seed"]))
        stream = int(cast(int, case["stream"]))
        expected_uint32 = cast(list[int], case["uint32"])
        rng = PCG32(seed, stream=stream)
        assert [rng.next_uint32() for _ in expected_uint32] == expected_uint32

        values = PCG32(seed, stream=stream).random((len(expected_uint32),))
        actual_bits = [f"0x{int(value):08x}" for value in values.view(np.uint32)]
        assert actual_bits == cast(list[str], case["float32_bits"])


def test_shared_naca_vectors_match_python_reference() -> None:
    document = cast(
        dict[str, object],
        json.loads(_fixture("naca2412.json").read_text(encoding="utf-8")),
    )
    foil_data = cast(dict[str, object], document["foil"])
    foil = NacaFoil(
        FoilSpec(
            naca=str(foil_data["naca"]),
            chord=float(cast(float, foil_data["chord"])),
            pivot=tuple(float(value) for value in cast(list[float], foil_data["pivot"])),
        )
    )
    tolerances = cast(dict[str, float], document["absolute_tolerances"])
    surface_x = np.asarray(cast(list[float], document["surface_x"]), dtype=np.float64)
    expected_upper = np.asarray(
        cast(list[float], document["surface_upper"]), dtype=np.float64
    )
    expected_lower = np.asarray(
        cast(list[float], document["surface_lower"]), dtype=np.float64
    )
    upper, lower = foil.surfaces(surface_x)
    np.testing.assert_allclose(upper, expected_upper, atol=tolerances["surface"])
    np.testing.assert_allclose(lower, expected_lower, atol=tolerances["surface"])

    for query in cast(list[dict[str, object]], document["queries"]):
        points = np.asarray(cast(list[list[float]], query["points"]), dtype=np.float64)
        angle = float(cast(float, query["angle_degrees"]))
        distance = foil.signed_distance(points, angle)
        expected_distance = np.asarray(
            cast(list[float], query["signed_distance"]), dtype=np.float64
        )
        expected_normals = np.asarray(
            cast(list[list[float]], query["normals"]), dtype=np.float64
        )
        expected_contains = np.asarray(cast(list[bool], query["contains"]), dtype=np.bool_)
        np.testing.assert_allclose(
            distance,
            expected_distance,
            atol=tolerances["signed_distance"],
        )
        np.testing.assert_allclose(
            foil.normals(points, angle),
            expected_normals,
            atol=tolerances["normal"],
        )
        np.testing.assert_array_equal(distance <= 0.0, expected_contains)


def test_shared_canonical_state_loads_with_declared_layout() -> None:
    state = load_canonical_state(_fixture("canonical-state-f32"))
    assert state.source_language == "conformance"
    assert state.source_solver == "golden"
    assert state.resolution == (4, 3)
    assert state.periodic_axes == ("x",)
    assert state.velocity.shape == (1, 3, 4, 2)
    assert state.velocity.dtype == np.float32
    assert state.density is not None


def test_shared_fortran_canonical_state_loads_with_declared_layout() -> None:
    state = load_canonical_state(_fixture("canonical-state-f32-fortran"))
    assert state.velocity.flags.f_contiguous
    assert state.density is not None
    assert state.density.flags.f_contiguous
    assert np.isclose(state.velocity[0, 2, 3, 1], np.float32(15.0 / 7.0))
    assert state.density.shape == (1, 3, 4)
