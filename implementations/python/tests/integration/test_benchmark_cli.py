import json
from pathlib import Path
from typing import cast

import pytest

from foilbench_py.benchmark.compare import format_comparison
from foilbench_py.benchmark.runner import recovery_window, run_matrix
from foilbench_py.cli import main
from foilbench_py.core.scenario import find_repo_root, load_scenario
from foilbench_py.solvers.factory import solver_ids


def test_smoke_benchmark_emits_comparable_artifacts() -> None:
    root = find_repo_root(Path(__file__))
    matrix_path = root / "benchmark-matrices" / "test.json"
    output = run_matrix(matrix_path, root / "results" / "test-artifacts")
    result_files = sorted(output.glob("*.json"))
    assert len(result_files) == 3
    result = cast(dict[str, object], json.loads(result_files[0].read_text(encoding="utf-8")))
    assert result["contract_id"] == "foilbench-phase2-v1"
    assert result["contract_revision"] == 2
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

    first = dict(result)
    second = dict(result)
    second["language"] = "julia"
    second["reynolds"] = cast(float, second["reynolds"]) * 2.0
    mismatch_directory = root / "results" / "test-artifacts-mismatched"
    mismatch_directory.mkdir(parents=True, exist_ok=True)
    (mismatch_directory / "first.json").write_text(json.dumps(first), encoding="utf-8")
    (mismatch_directory / "second.json").write_text(json.dumps(second), encoding="utf-8")
    with pytest.raises(ValueError, match="different physical inputs"):
        format_comparison(mismatch_directory)


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
