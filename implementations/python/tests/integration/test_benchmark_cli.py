import json
from pathlib import Path
from typing import cast

from foilbench_py.benchmark.compare import format_comparison
from foilbench_py.benchmark.runner import run_matrix
from foilbench_py.core.scenario import find_repo_root


def test_smoke_benchmark_emits_comparable_artifacts() -> None:
    root = find_repo_root(Path(__file__))
    matrix_path = root / "benchmark-matrices" / "test.json"
    output = run_matrix(matrix_path, root / "results" / "test-artifacts")
    result_files = sorted(output.glob("*.json"))
    assert len(result_files) == 3
    result = cast(dict[str, object], json.loads(result_files[0].read_text(encoding="utf-8")))
    assert result["resolution"] == [32, 16]
    assert cast(float, result["cell_updates_per_second"]) > 0.0
    assert cast(float, result["particle_updates_per_second"]) >= 0.0
    diagnostics = cast(dict[str, float], result["diagnostics"])
    assert diagnostics["wake_probe_samples"] >= 8.0
    assert diagnostics["wake_frequency_resolution"] > 0.0
    assert diagnostics["wake_dominant_frequency"] >= 0.0
    assert 0.0 <= diagnostics["wake_dominant_power_fraction"] <= 1.0
    comparison = format_comparison(output)
    assert "stable-fluids" in comparison
    assert "lbm-d2q9" in comparison
    assert "pic-flip" in comparison
