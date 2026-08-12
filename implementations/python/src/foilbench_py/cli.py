"""FoilBench Python command-line interface."""

import argparse
import json
from collections.abc import Sequence
from pathlib import Path

from foilbench_py import __version__
from foilbench_py.benchmark.chaos_acceptance import validate_chaos_acceptance
from foilbench_py.benchmark.compare import format_comparison
from foilbench_py.benchmark.runner import run_matrix
from foilbench_py.core.scenario import load_scenario
from foilbench_py.solvers.factory import create_solver, solver_ids
from foilbench_py.viewer.app import run_viewer


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="foilbench-py")
    parser.add_argument("--version", action="version", version=__version__)
    subcommands = parser.add_subparsers(dest="command", required=True)

    describe = subcommands.add_parser("describe", help="describe available capabilities")
    describe.add_argument("--json", action="store_true", dest="as_json")

    view = subcommands.add_parser("view", help="open the native interactive viewer")
    view.add_argument("scenario", type=Path)
    view.add_argument("--solver", choices=solver_ids(), default="stable-fluids")

    bench = subcommands.add_parser("bench", help="run a benchmark matrix")
    bench.add_argument("matrix", type=Path)
    bench.add_argument("--output", type=Path)

    compare = subcommands.add_parser("compare", help="compare result artifacts")
    compare.add_argument("results", type=Path)
    compare.add_argument("--require-complete", action="store_true")

    chaos = subcommands.add_parser(
        "chaos-validate", help="validate optional chaotic-wake result artifacts"
    )
    chaos.add_argument("artifacts", nargs="+", type=Path)
    return parser


def _describe(as_json: bool) -> None:
    entries: list[dict[str, object]] = []
    for solver_id in solver_ids():
        info = create_solver(solver_id).info
        entries.append(
            {
                "id": info.id,
                "display_name": info.display_name,
                "dimensions": info.dimensions,
                "moving_boundary": info.supports_moving_boundary,
                "acceleration": info.acceleration,
            }
        )
    description: dict[str, object] = {
        "implementation": "python",
        "version": __version__,
        "canonical_reference": True,
        "thin_3d": False,
        "solvers": entries,
    }
    if as_json:
        print(json.dumps(description, indent=2))
    else:
        print("FoilBench Python reference")
        for entry in entries:
            print(f"  {entry['id']:<18} {entry['display_name']} [{entry['acceleration']}]")
        print("  thin periodic 3D: declared by schema, unsupported in Phase 1")


def main(argv: Sequence[str] | None = None) -> None:
    arguments = _parser().parse_args(argv)
    if arguments.command == "describe":
        _describe(bool(arguments.as_json))
    elif arguments.command == "view":
        scenario = load_scenario(arguments.scenario)
        run_viewer(scenario, str(arguments.solver))
    elif arguments.command == "bench":
        output = run_matrix(arguments.matrix, arguments.output)
        print(output)
    elif arguments.command == "compare":
        print(format_comparison(arguments.results, require_complete=arguments.require_complete))
    elif arguments.command == "chaos-validate":
        print(validate_chaos_acceptance(arguments.artifacts))
    else:
        raise AssertionError(f"unhandled command {arguments.command}")


if __name__ == "__main__":
    main()
