"""Executable Revision 4 classification for optional chaotic-wake artifacts."""

import json
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import cast

from foilbench_py.core._schema_adapter import validate_json
from foilbench_py.core.scenario import find_repo_root


def _documents(paths: Sequence[Path]) -> list[dict[str, object]]:
    selected: list[dict[str, object]] = []
    for path in paths:
        value = json.loads(path.read_text(encoding="utf-8"))
        values = value if isinstance(value, list) else [value]
        for entry in values:
            if not isinstance(entry, dict):
                raise TypeError(f"{path} does not contain chaotic-wake result objects")
            selected.append(cast(dict[str, object], entry))
    return selected


def validate_chaos_acceptance(paths: Sequence[Path]) -> str:
    """Validate complete participation and classify every declared sweep case."""

    if not paths:
        raise ValueError("at least one chaotic-wake artifact is required")
    root = find_repo_root(Path(__file__))
    acceptance = cast(
        dict[str, object],
        json.loads(
            (root / "spec/conformance/fullsize-acceptance.json").read_text(
                encoding="utf-8"
            )
        ),
    )
    cases = cast(
        dict[str, object],
        json.loads(
            (root / "spec/conformance/chaotic-wake-cases.json").read_text(
                encoding="utf-8"
            )
        ),
    )
    schema = cast(
        dict[str, object],
        json.loads(
            (root / "spec/chaotic-wake-result.schema.json").read_text(encoding="utf-8")
        ),
    )
    thresholds = cast(Mapping[str, object], acceptance["chaotic_extension"])
    expected_cases = {
        (
            float(cast(float, entry["reynolds"])),
            float(cast(float, entry["angle_degrees"])),
            tuple(cast(list[int], entry["resolution"])),
        )
        for entry in cast(list[dict[str, object]], cast(dict[str, object], cases["sweep"])["cases"])
    }
    documents = _documents(paths)
    grouped: dict[str, list[dict[str, object]]] = {}
    for document in documents:
        validate_json(document, schema)
        grouped.setdefault(str(document["language"]), []).append(document)
    failures: list[str] = []
    for language, entries in grouped.items():
        observed_cases: set[tuple[float, float, tuple[int, ...]]] = set()
        sensitivity_count = 0
        for entry in entries:
            if entry["experiment"] == "chaotic-wake-sensitivity":
                sensitivity_count += 1
                continue
            parameters = cast(Mapping[str, object], entry["parameters"])
            metrics = cast(Mapping[str, object], entry["metrics"])
            key = (
                float(cast(float, parameters["reynolds"])),
                float(cast(float, parameters["angle_degrees"])),
                tuple(cast(list[int], parameters["resolution"])),
            )
            if key in observed_cases:
                failures.append(f"{language}: duplicate sweep case {key!r}")
            observed_cases.add(key)
            checks = (
                ("probe_rms", ">=", "minimum_probe_rms"),
                ("spectral_entropy", ">=", "minimum_spectral_entropy"),
                ("broadband_power_fraction", ">=", "minimum_broadband_power_fraction"),
                (
                    "enstrophy_coefficient_of_variation",
                    ">=",
                    "minimum_enstrophy_coefficient_of_variation",
                ),
                ("dominant_power_fraction", "<=", "maximum_dominant_power_fraction"),
            )
            for metric, relation, threshold_name in checks:
                value = float(cast(float, metrics[metric]))
                limit = float(cast(float, thresholds[threshold_name]))
                accepted = value >= limit if relation == ">=" else value <= limit
                if not accepted:
                    failures.append(
                        f"{language} {key!r}: {metric}={value:.6g} {relation} {limit:.6g} failed"
                    )
        if observed_cases != expected_cases:
            failures.append(
                f"{language}: sweep participation mismatch "
                f"missing={sorted(expected_cases - observed_cases)!r} "
                f"extra={sorted(observed_cases - expected_cases)!r}"
            )
        if sensitivity_count != 1:
            failures.append(
                f"{language}: expected one sensitivity artifact, found {sensitivity_count}"
            )
    if failures:
        raise ValueError("chaotic-wake acceptance failed:\n" + "\n".join(failures))
    return f"Chaotic-wake acceptance passed for {', '.join(sorted(grouped))}."
