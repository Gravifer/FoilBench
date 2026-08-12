"""Scenario and schema loading."""

import json
from pathlib import Path
from typing import Literal, cast

from foilbench_py.core._schema_adapter import validate_json
from foilbench_py.core.models import (
    AxisName,
    ControlKeyframe,
    DomainSpec,
    FoilSpec,
    Precision,
    Scenario,
)


def find_repo_root(start: Path | None = None) -> Path:
    current = (start or Path.cwd()).resolve()
    if current.is_file():
        current = current.parent
    for candidate in (current, *current.parents):
        if (candidate / "spec" / "schemas" / "scenario.schema.json").exists():
            return candidate
    raise FileNotFoundError("could not locate FoilBench repository root")


def _load_object(path: Path) -> dict[str, object]:
    loaded = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(loaded, dict):
        raise TypeError(f"{path} must contain a JSON object")
    return cast(dict[str, object], loaded)


def load_scenario(path: str | Path) -> Scenario:
    scenario_path = Path(path).resolve()
    raw = _load_object(scenario_path)
    root = find_repo_root(scenario_path)
    schema = _load_object(root / "spec" / "schemas" / "scenario.schema.json")
    validate_json(raw, schema)

    dimension = cast(int, raw["dimension"])
    raw_bounds = cast(list[list[float]], raw["bounds"])
    raw_resolution = cast(list[int], raw["resolution"])
    raw_foil = cast(dict[str, object], raw["foil"])
    raw_controls = cast(list[dict[str, object]], raw["controls"])

    domain = DomainSpec(
        dimension=cast(Literal[2, 3], dimension),
        bounds=tuple((float(pair[0]), float(pair[1])) for pair in raw_bounds),
        resolution=tuple(int(value) for value in raw_resolution),
        periodic_axes=tuple(
            cast(AxisName, value) for value in cast(list[str], raw["periodic_axes"])
        ),
    )
    foil = FoilSpec(
        naca=str(raw_foil["naca"]),
        chord=float(cast(float, raw_foil["chord"])),
        pivot=tuple(float(value) for value in cast(list[float], raw_foil["pivot"])),
    )
    controls = tuple(
        ControlKeyframe(
            time=float(cast(float, item["time"])),
            angle_degrees=float(cast(float, item["angle_degrees"])),
        )
        for item in raw_controls
    )
    return Scenario(
        schema_version=int(cast(int, raw["schema_version"])),
        id=str(raw["id"]),
        domain=domain,
        reynolds=float(cast(float, raw["reynolds"])),
        freestream=tuple(float(value) for value in cast(list[float], raw["freestream"])),
        foil=foil,
        controls=controls,
        duration=float(cast(float, raw["duration"])),
        output_dt=float(cast(float, raw["output_dt"])),
        precision=cast(Precision, raw["precision"]),
        seed=int(cast(int, raw["seed"])),
        solver_options=cast(dict[str, object], raw.get("solver_options", {})),
    )
