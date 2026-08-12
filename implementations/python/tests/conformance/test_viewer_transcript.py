import json
from pathlib import Path
from typing import Literal, cast

import pytest

from foilbench_py.core._schema_adapter import validate_json
from foilbench_py.core.scenario import find_repo_root, load_scenario
from foilbench_py.viewer.app import ViewerModel

ActionKind = Literal[
    "step",
    "pause",
    "reset",
    "set-angle",
    "release-angle",
    "switch",
    "set-reynolds",
    "toggle-diagnostics",
    "shutdown",
]


def _object(path: Path) -> dict[str, object]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise TypeError(f"{path} must contain an object")
    return cast(dict[str, object], value)


def test_shared_viewer_transcript() -> None:
    root = find_repo_root()
    transcript = _object(root / "spec" / "conformance" / "viewer-basic.json")
    validate_json(transcript, _object(root / "spec" / "schemas" / "viewer-transcript.schema.json"))
    scenario = load_scenario(root / cast(str, transcript["scenario"]))
    model = ViewerModel.create(scenario, cast(str, transcript["solver"]))
    stopped = False

    for raw_action in cast(list[dict[str, object]], transcript["actions"]):
        kind = cast(ActionKind, raw_action["kind"])
        previous_time = model.time
        if kind == "step":
            model.update(scenario.output_dt)
        elif kind == "pause":
            model.paused = not model.paused
        elif kind == "reset":
            model.reset()
        elif kind == "set-angle":
            model.set_angle(
                cast(float, raw_action["angle_degrees"]),
                cast(float, raw_action["at"]),
            )
        elif kind == "release-angle":
            model.release_angle()
        elif kind == "switch":
            model.switch_solver(cast(str, raw_action["solver"]))
        elif kind == "set-reynolds":
            model.set_reynolds(cast(float, raw_action["reynolds"]))
        elif kind == "toggle-diagnostics":
            model.toggle_diagnostics()
        else:
            stopped = True

        expected = cast(dict[str, object], raw_action["expect"])
        state = model.session_state
        phase = "stopped" if stopped else state.phase
        if "phase" in expected:
            assert phase == expected["phase"]
        if "motion_mode" in expected:
            assert state.motion_mode == expected["motion_mode"]
        if "diagnostic_mode" in expected:
            assert state.diagnostic_mode == expected["diagnostic_mode"]
        if "schedule_active" in expected:
            assert state.schedule_active is expected["schedule_active"]
        if "angle_degrees" in expected:
            assert model.control(0.0).angle_degrees == pytest.approx(
                cast(float, expected["angle_degrees"])
            )
        relation = expected.get("time_relation")
        if relation == "advanced":
            assert model.time > previous_time
        elif relation == "unchanged":
            assert model.time == previous_time
        elif relation == "reset":
            assert model.time == 0.0
