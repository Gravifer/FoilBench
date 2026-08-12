import json
from pathlib import Path
from typing import cast

import pytest

from foilbench_py.benchmark.artifact import validate_result_semantics
from foilbench_py.benchmark.compare import format_comparison
from foilbench_py.benchmark.runner import recovery_window, run_matrix
from foilbench_py.cli import main
from foilbench_py.core.scenario import find_repo_root, load_scenario
from foilbench_py.solvers.factory import solver_ids


def test_smoke_benchmark_emits_comparable_artifacts(tmp_path: Path) -> None:
    root = find_repo_root(Path(__file__))
    matrix_path = root / "benchmark-matrices" / "test.json"
    output = run_matrix(matrix_path, tmp_path / "test-artifacts")
    result_files = sorted(output.glob("*-r1.json"))
    assert len(result_files) == 3
    result = cast(dict[str, object], json.loads(result_files[0].read_text(encoding="utf-8")))
    assert result["contract_id"] == "foilbench-phase2-v1"
    assert result["contract_revision"] == 4
    assert result["repetition"] == 1
    assert cast(float, result["effective_reynolds"]) > 0.0
    assert isinstance(result["solver_configuration"], dict)
    assert result["resolution"] == [32, 16]
    assert result["memory_measurement"] == "rss"
    assert cast(float, result["cell_updates_per_second"]) > 0.0
    assert cast(float, result["particle_updates_per_second"]) >= 0.0
    assert cast(float, result["initialization_seconds"]) > 0.0
    assert cast(float, result["cold_step_seconds"]) > 0.0
    diagnostics = cast(dict[str, float], result["diagnostics"])
    assert diagnostics["wake_probe_samples"] >= 8.0
    assert diagnostics["wake_frequency_resolution"] > 0.0
    assert diagnostics["wake_dominant_frequency"] >= 0.0
    assert 0.0 <= diagnostics["wake_dominant_power_fraction"] <= 1.0
    last_step = cast(dict[str, object], result["last_step"])
    assert result["failure"] is None
    assert result["success"] is True
    assert result["final_state_revision"] == result["diagnostic_state_revision"]
    assert result["final_state_revision"] == last_step["state_revision"]
    assert cast(dict[str, object], last_step["evidence"])
    comparison = format_comparison(output)
    assert "stable-fluids" in comparison
    assert "lbm-d2q9" in comparison
    assert "pic-flip" in comparison
    assert "stable-fluids" in format_comparison(output, require_complete=True)
    assert "stable-fluids" in format_comparison(
        output, required_languages=("python",)
    )
    with pytest.raises(ValueError, match="producer roster mismatch"):
        format_comparison(output, required_languages=("python", "julia"))
    missing_directory = tmp_path / "test-artifacts-incomplete"
    missing_directory.mkdir()
    (missing_directory / result_files[0].name).write_text(
        result_files[0].read_text(encoding="utf-8"), encoding="utf-8"
    )
    with pytest.raises(ValueError, match="incomplete python artifacts"):
        format_comparison(missing_directory, require_complete=True)

    first = dict(result)
    second = dict(result)
    second["language"] = "julia"
    second["reynolds"] = cast(float, second["reynolds"]) * 2.0
    mismatch_directory = tmp_path / "test-artifacts-mismatched"
    mismatch_directory.mkdir(parents=True, exist_ok=True)
    (mismatch_directory / "first.json").write_text(json.dumps(first), encoding="utf-8")
    (mismatch_directory / "second.json").write_text(json.dumps(second), encoding="utf-8")
    with pytest.raises(ValueError, match="different physical inputs"):
        format_comparison(mismatch_directory)

    equivalent = dict(result)
    equivalent["language"] = "typescript"
    equivalent["reynolds"] = int(cast(float, result["reynolds"]))
    equivalent["foil"] = {
        "pivot": cast(dict[str, object], result["foil"])["pivot"],
        "chord": cast(dict[str, object], result["foil"])["chord"],
        "naca": cast(dict[str, object], result["foil"])["naca"],
    }
    equivalent["output_dt"] = cast(float, result["output_dt"]) * (1.0 + 5.0e-7)
    equivalent["effective_reynolds"] = cast(
        float, result["effective_reynolds"]
    ) * (1.0 - 5.0e-7)
    (output / "equivalent.json").write_text(
        json.dumps(equivalent), encoding="utf-8"
    )
    format_comparison(output)
    assert validate_result_semantics(equivalent) is None

    equivalent["output_dt"] = cast(float, result["output_dt"]) * 1.01
    (output / "equivalent.json").write_text(
        json.dumps(equivalent), encoding="utf-8"
    )
    with pytest.raises(ValueError, match="different physical inputs"):
        format_comparison(output)


def test_result_semantics_reject_cross_field_contradictions(tmp_path: Path) -> None:
    root = find_repo_root(Path(__file__))
    output = run_matrix(
        root / "benchmark-matrices" / "test.json",
        tmp_path / "test-artifacts-semantics",
    )
    result = cast(
        dict[str, object],
        json.loads(next(output.glob("*.json")).read_text(encoding="utf-8")),
    )
    contradictory = dict(result)
    contradictory["failure"] = {
        "kind": "unexpected",
        "reason": None,
        "stage": None,
        "message": "impossible",
        "evidence": {},
    }
    with pytest.raises(ValueError, match="completed-step semantics"):
        validate_result_semantics(contradictory)
    stale = dict(result)
    stale["diagnostic_state_revision"] = -1
    with pytest.raises(ValueError, match="stale revision"):
        validate_result_semantics(stale)
    inconsistent = dict(result)
    inconsistent["median_step_seconds"] = 123.0
    with pytest.raises(ValueError, match="inconsistent derived field"):
        validate_result_semantics(inconsistent)


def test_describe_reports_python_capabilities(capsys: pytest.CaptureFixture[str]) -> None:
    main(["describe", "--json"])
    captured = capsys.readouterr()
    description = cast(dict[str, object], json.loads(captured.out))
    assert description["implementation"] == "python"
    assert description["thin_3d"] is False
    solvers = cast(list[dict[str, object]], description["solvers"])
    assert tuple(entry["id"] for entry in solvers) == solver_ids()
    assert all(entry["dimensions"] == [2] for entry in solvers)
    assert all(entry["moving_boundary"] is True for entry in solvers)


def test_default_scenario_declares_a_recovery_window() -> None:
    root = find_repo_root(Path(__file__))
    scenario = load_scenario(root / "scenarios" / "airfoil" / "default.json")
    assert recovery_window(scenario) == (3.0, 18.0)
