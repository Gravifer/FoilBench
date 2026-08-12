import json
from pathlib import Path
from typing import cast

import pytest

from foilbench_py.benchmark.chaos_acceptance import (
    validate_chaos_acceptance,
    validate_chaos_preflight,
)
from foilbench_py.core.scenario import find_repo_root


def test_chaos_acceptance_enforces_thresholds_and_participation(tmp_path: Path) -> None:
    root = find_repo_root(Path(__file__))
    fixture = cast(
        dict[str, object],
        json.loads(
            (root / "spec/conformance/chaotic-wake-cases.json").read_text(
                encoding="utf-8"
            )
        ),
    )
    sweep: list[dict[str, object]] = []
    for case in cast(list[dict[str, object]], cast(dict[str, object], fixture["sweep"])["cases"]):
        sweep.append(
            {
                "schema_version": 1,
                "contract_id": "foilbench-phase2-v1",
                "contract_revision": 4,
                "experiment": "chaotic-wake-sweep",
                "language": "python",
                "solver": "stable-fluids",
                "scenario": "chaotic-airfoil-experimental",
                "parameters": {**case, "duration": 12.0, "burn_in": 4.0},
                "metrics": {
                    "probe_rms": 0.06,
                    "spectral_entropy": 0.2,
                    "dominant_power_fraction": 0.8,
                    "broadband_power_fraction": 0.1,
                    "decorrelation_time": 1.0,
                    "enstrophy_mean": 1.0,
                    "enstrophy_coefficient_of_variation": 0.1,
                    "maximum_speed": 2.0,
                    "vorticity_small_scale_fraction": 0.1,
                },
                "wall_seconds": 1.0,
            }
        )
    sensitivity = {
        "schema_version": 1,
        "contract_id": "foilbench-phase2-v1",
        "contract_revision": 4,
        "experiment": "chaotic-wake-sensitivity",
        "language": "python",
        "solver": "stable-fluids",
        "scenario": "chaotic-airfoil-experimental",
        "parameters": {
            **cast(
                dict[str, object],
                cast(dict[str, object], fixture["sensitivity"])["case"],
            ),
            "duration": 12.0,
            "epsilon": 1.0e-4,
        },
        "metrics": {
            "initial_wake_rms_difference": 8.0e-6,
            "final_wake_rms_difference": 0.1,
            "maximum_wake_rms_difference": 0.1,
            "amplification": 12500.0,
            "finite_time_exponent": 1.0,
            "exponential_fit_r_squared": 0.5,
            "exponential_fit_samples": 10,
        },
        "initialization": {
            "reference_import_status": "accepted",
            "perturbed_import_status": "accepted",
            "authoritative_angle_degrees": 35.0,
            "requested_epsilon": 1.0e-4,
            "realized_post_import_wake_rms_difference": 8.0e-6,
            "realized_to_requested_ratio": 0.08,
        },
        "series": {"times": [12.0], "wake_rms_differences": [0.1]},
        "wall_seconds": 1.0,
    }
    sweep_path = tmp_path / "sweep.json"
    sensitivity_path = tmp_path / "sensitivity.json"
    sweep_path.write_text(json.dumps(sweep), encoding="utf-8")
    sensitivity_path.write_text(json.dumps(sensitivity), encoding="utf-8")
    assert "python" in validate_chaos_acceptance([sweep_path, sensitivity_path])
    assert "python" in validate_chaos_acceptance(
        [sweep_path, sensitivity_path], required_languages=("python",)
    )
    with pytest.raises(ValueError, match="producer roster mismatch"):
        validate_chaos_acceptance(
            [sweep_path, sensitivity_path],
            required_languages=("python", "julia", "typescript"),
        )

    cast(dict[str, float], sweep[0]["metrics"])["probe_rms"] = 0.0
    sweep_path.write_text(json.dumps(sweep), encoding="utf-8")
    with pytest.raises(ValueError, match="probe_rms"):
        validate_chaos_acceptance([sweep_path, sensitivity_path])


def test_chaos_preflight_enforces_initialization_ratio_and_roster(tmp_path: Path) -> None:
    preflight = {
        "schema_version": 1,
        "contract_id": "foilbench-phase2-v1",
        "contract_revision": 4,
        "experiment": "chaotic-wake-sensitivity",
        "language": "python",
        "solver": "stable-fluids",
        "scenario": "chaotic-airfoil-experimental",
        "parameters": {
            "reynolds": 10_000.0,
            "angle_degrees": 35.0,
            "resolution": [160, 96],
            "duration": 0.1,
            "epsilon": 1.0e-4,
        },
        "metrics": {
            "initial_wake_rms_difference": 8.0e-6,
            "final_wake_rms_difference": 9.0e-6,
            "maximum_wake_rms_difference": 9.0e-6,
            "amplification": 1.125,
            "finite_time_exponent": 0.0,
            "exponential_fit_r_squared": 0.0,
            "exponential_fit_samples": 0,
        },
        "initialization": {
            "reference_import_status": "accepted",
            "perturbed_import_status": "accepted",
            "authoritative_angle_degrees": 35.0,
            "requested_epsilon": 1.0e-4,
            "realized_post_import_wake_rms_difference": 8.0e-6,
            "realized_to_requested_ratio": 0.08,
        },
        "series": {"times": [0.1], "wake_rms_differences": [9.0e-6]},
        "wall_seconds": 1.0,
    }
    path = tmp_path / "preflight.json"
    path.write_text(json.dumps(preflight), encoding="utf-8")
    assert "python" in validate_chaos_preflight(
        [path], required_languages=("python",)
    )
    cast(dict[str, float], preflight["initialization"])[
        "realized_to_requested_ratio"
    ] = 66.0
    path.write_text(json.dumps(preflight), encoding="utf-8")
    with pytest.raises(ValueError, match=r"internally inconsistent|outside"):
        validate_chaos_preflight([path])
