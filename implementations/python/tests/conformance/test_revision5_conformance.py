import copy
import json
from pathlib import Path
from typing import cast

import numpy as np
import pytest
from jsonschema.exceptions import ValidationError

from foilbench_py.core._schema_adapter import validate_json
from foilbench_py.core.geometry import NacaFoil
from foilbench_py.core.grid import apply_domain_boundaries
from foilbench_py.core.models import ControlState, DomainSpec, FoilSpec
from foilbench_py.core.scenario import (
    find_repo_root,
    load_scenario,
    load_scenario_document,
)
from foilbench_py.solvers.lbm import lbm_sponge_strength

ROOT = find_repo_root(Path(__file__))
FIXTURES = ROOT / "spec" / "conformance"
SCHEMAS = ROOT / "spec" / "schemas"


def _object(path: Path) -> dict[str, object]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise TypeError(f"{path} must contain an object")
    return cast(dict[str, object], value)


def _fixture(name: str) -> dict[str, object]:
    return _object(FIXTURES / name)


def _object_node(value: object) -> dict[str, object]:
    if not isinstance(value, dict):
        raise TypeError("fixture path requires an object")
    return cast(dict[str, object], value)


def _array_node(value: object) -> list[object]:
    if not isinstance(value, list):
        raise TypeError("fixture path requires an array")
    return cast(list[object], value)


def _mutate(document: dict[str, object], path: list[object], value: object) -> None:
    cursor: object = document
    for segment in path[:-1]:
        if isinstance(segment, str):
            cursor = _object_node(cursor)[segment]
        elif isinstance(segment, int):
            cursor = _array_node(cursor)[segment]
        else:
            raise TypeError("fixture path components must be strings or integers")
    final = path[-1]
    if isinstance(final, str):
        _object_node(cursor)[final] = value
    elif isinstance(final, int):
        _array_node(cursor)[final] = value
    else:
        raise TypeError("fixture path components must be strings or integers")


def test_revision5_geometry_fixture_matches_python() -> None:
    fixture = _fixture("geometry-v1.json")
    descriptor = cast(dict[str, object], fixture["descriptor"])
    foil = NacaFoil(
        FoilSpec(
            naca=str(descriptor["naca"]),
            chord=float(cast(float, descriptor["chord"])),
            pivot=tuple(float(value) for value in cast(list[float], descriptor["pivot"])),
        )
    )
    tolerances = cast(dict[str, float], fixture["absolute_tolerances"])
    assert len(cast(list[object], fixture["surface_x"])) == len(
        cast(list[object], fixture["surface_upper"])
    ) == len(cast(list[object], fixture["surface_lower"]))
    assert len(cast(list[object], fixture["points"])) == len(
        cast(list[object], fixture["signed_distance"])
    ) == len(cast(list[object], fixture["normals"])) == len(
        cast(list[object], fixture["contains"])
    ) == len(cast(list[object], fixture["wall_velocity"]))
    surface_x = np.asarray(cast(list[float], fixture["surface_x"]), dtype=np.float64)
    upper, lower = foil.surfaces(surface_x)
    np.testing.assert_allclose(
        upper,
        cast(list[float], fixture["surface_upper"]),
        atol=tolerances["surface"],
    )
    np.testing.assert_allclose(
        lower,
        cast(list[float], fixture["surface_lower"]),
        atol=tolerances["surface"],
    )

    points = np.asarray(cast(list[list[float]], fixture["points"]), dtype=np.float64)
    angle = float(cast(float, fixture["angle_degrees"]))
    np.testing.assert_allclose(
        foil.signed_distance(points, angle),
        cast(list[float], fixture["signed_distance"]),
        atol=tolerances["signed_distance"],
    )
    np.testing.assert_allclose(
        foil.normals(points, angle),
        cast(list[list[float]], fixture["normals"]),
        atol=tolerances["normal"],
    )
    np.testing.assert_array_equal(
        foil.contains(points, angle), cast(list[bool], fixture["contains"])
    )
    control = ControlState(
        time=0.0,
        angle_degrees=angle,
        angular_velocity_degrees=float(cast(float, fixture["angular_velocity_degrees"])),
    )
    np.testing.assert_allclose(
        foil.wall_velocity(points, control),
        cast(list[list[float]], fixture["wall_velocity"]),
        atol=tolerances["wall_velocity"],
    )
    assert foil.maximum_radius == pytest.approx(
        float(cast(float, fixture["maximum_radius"])), abs=tolerances["radius"]
    )


def test_revision5_manifest_and_fidelity_inventory_are_consumable() -> None:
    manifest = _fixture("canonical-manifest-v2.json")
    schema = _object(SCHEMAS / "canonical-manifest-v2.schema.json")
    validate_json(manifest, schema)
    assert cast(dict[str, object], manifest["geometry"])["family"] == "naca-four-digit-v1"
    producer = cast(dict[str, object], manifest["producer"])
    assert producer == {"implementation": "rust", "execution_target": "native", "build": None}

    fidelity = _fixture("fidelity-cases.json")
    for case in cast(list[dict[str, object]], fidelity["cases"]):
        scenario = load_scenario(ROOT / str(case["scenario"]))
        assert len(cast(list[int], case["resolution"])) == scenario.domain.dimension
        assert float(cast(float, case["duration"])) > 0.0
        assert cast(dict[str, object], case["metrics"])


def test_revision5_fidelity_schema_rejects_wrong_metric_rosters_and_thresholds() -> None:
    fidelity = _fixture("fidelity-cases.json")
    schema = _object(SCHEMAS / "fidelity-cases.schema.json")

    wrong_roster = copy.deepcopy(fidelity)
    uniform = cast(list[dict[str, object]], wrong_roster["cases"])[0]
    cast(dict[str, object], uniform["metrics"])["unexpected"] = {
        "comparison": "finite",
        "threshold": None,
    }
    with pytest.raises(ValidationError):
        validate_json(wrong_roster, schema)

    wrong_threshold = copy.deepcopy(fidelity)
    uniform = cast(list[dict[str, object]], wrong_threshold["cases"])[0]
    metric = cast(
        dict[str, object],
        cast(dict[str, object], uniform["metrics"])["velocity_rms_drift"],
    )
    metric["threshold"] = None
    with pytest.raises(ValidationError):
        validate_json(wrong_threshold, schema)


def test_revision5_mac_and_lbm_boundary_fixtures_match_python() -> None:
    mac = _fixture("mac-boundary.json")
    assert mac["periodic_duplicate"] == "endpoint-average"
    domain = DomainSpec(
        dimension=2,
        bounds=((0.0, 2.0), (-1.0, 1.0)),
        resolution=(8, 6),
        periodic_axes=(),
    )
    u = np.full((domain.ny, domain.nx + 1), -3.0)
    v = np.full((domain.ny + 1, domain.nx), -4.0)
    apply_domain_boundaries(u, v, domain, (1.25, -0.5))
    np.testing.assert_allclose(u[:, 0], 1.25)
    np.testing.assert_allclose(u[0, :], 1.25)
    np.testing.assert_allclose(u[-1, :], 1.25)
    np.testing.assert_allclose(v[0, :], -0.5)
    np.testing.assert_allclose(v[-1, :], -0.5)

    lbm = _fixture("lbm-boundary.json")
    sponge = cast(dict[str, object], lbm["sponge"])
    assert lbm_sponge_strength(
        160, 96, 80, 0, periodic_x=False, periodic_y=False
    ) == pytest.approx(float(cast(float, sponge["transverse_maximum"])))
    assert lbm_sponge_strength(
        160, 96, 159, 48, periodic_x=False, periodic_y=False
    ) == pytest.approx(float(cast(float, sponge["outlet_maximum"])))
    assert lbm_sponge_strength(
        160, 96, 159, 0, periodic_x=False, periodic_y=False
    ) == pytest.approx(
        max(
            float(cast(float, sponge["transverse_maximum"])),
            float(cast(float, sponge["outlet_maximum"])),
        )
    )
    assert lbm_sponge_strength(
        160,
        96,
        80,
        0,
        periodic_x=False,
        periodic_y=False,
        channel_walls=True,
    ) == 0.0


def test_revision5_negative_scenarios_are_rejected() -> None:
    fixture = _fixture("scenario-negative.json")
    scenario_schema = _object(ROOT / "spec" / "schemas" / "scenario.schema.json")
    for case in cast(list[dict[str, object]], fixture["cases"]):
        document = copy.deepcopy(_object(ROOT / str(case["base"])))
        _mutate(document, cast(list[object], case["path"]), case["value"])
        with pytest.raises((TypeError, ValueError, ValidationError)):
            load_scenario_document(document, scenario_schema)
