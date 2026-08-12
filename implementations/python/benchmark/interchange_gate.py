"""Read every producer's canonical snapshots and import them into Python solvers."""

import json
import sys
from dataclasses import replace
from pathlib import Path
from typing import cast

from foilbench_py.core.geometry import NacaFoil
from foilbench_py.core.models import ControlState
from foilbench_py.core.scenario import find_repo_root, load_scenario
from foilbench_py.core.state_io import load_canonical_state
from foilbench_py.solvers.factory import create_solver, solver_ids


def main() -> None:
    if len(sys.argv) != 2:
        raise SystemExit("usage: interchange_gate.py RESULTS_ROOT")
    root = find_repo_root(Path(__file__))
    results = Path(sys.argv[1]).resolve()
    scenario_base = load_scenario(root / "scenarios/airfoil/default.json")
    expected = {
        (language, solver_id)
        for language in ("python", "julia", "typescript")
        for solver_id in solver_ids()
    }
    observed: set[tuple[str, str]] = set()
    for manifest_path in sorted(results.rglob("manifest.json")):
        manifest = cast(
            dict[str, object],
            json.loads(manifest_path.read_text(encoding="utf-8")),
        )
        source = (str(manifest["source_language"]), str(manifest["source_solver"]))
        if source in observed:
            raise ValueError(f"duplicate canonical snapshot from {source!r}")
        observed.add(source)
        state = load_canonical_state(manifest_path.parent)
        scenario = replace(
            scenario_base,
            domain=replace(scenario_base.domain, resolution=state.resolution),
        )
        control = ControlState(
            state.time, state.angle_degrees, state.angular_velocity_degrees
        )
        geometry = NacaFoil(scenario.foil)
        for destination_id in solver_ids():
            destination = create_solver(destination_id)
            destination.initialize(scenario, geometry, scenario.seed)
            outcome = destination.import_state(state, control)
            if outcome.status != "accepted":
                raise RuntimeError(
                    f"Python rejected {source!r} in {destination_id}: "
                    f"{outcome.reason} at {outcome.stage}"
                )
    if observed != expected:
        raise ValueError(
            f"canonical producer roster mismatch: missing={sorted(expected - observed)!r} "
            f"extra={sorted(observed - expected)!r}"
        )
    print("Python imported all 27 cross-language canonical conversions")


if __name__ == "__main__":
    main()
