"""Executable Revision 4 classification for optional chaotic-wake artifacts."""

import json
import math
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import cast

from foilbench_py.core._schema_adapter import validate_json
from foilbench_py.core.scenario import find_repo_root


def _documents(paths: Sequence[Path]) -> list[dict[str, object]]:
    selected: list[dict[str, object]] = []
    for path in paths:
        value = cast(object, json.loads(path.read_text(encoding="utf-8")))
        values = cast(list[object], value) if isinstance(value, list) else [value]
        for entry in values:
            if not isinstance(entry, dict):
                raise TypeError(f"{path} does not contain chaotic-wake result objects")
            selected.append(cast(dict[str, object], entry))
    return selected


def _check_sensitivity_initialization(
    document: Mapping[str, object],
    expected: Mapping[str, object],
    ratio_bounds: Mapping[str, object],
    failures: list[str],
) -> None:
    language = str(document["language"])
    parameters = cast(Mapping[str, object], document["parameters"])
    metrics = cast(Mapping[str, object], document["metrics"])
    initialization = cast(Mapping[str, object], document["initialization"])
    expected_case = cast(Mapping[str, object], expected["case"])
    expected_values = (
        float(cast(float, expected_case["reynolds"])),
        float(cast(float, expected_case["angle_degrees"])),
        tuple(cast(list[int], expected_case["resolution"])),
        float(cast(float, expected["duration"])),
        float(cast(float, expected["epsilon"])),
    )
    observed_values = (
        float(cast(float, parameters["reynolds"])),
        float(cast(float, parameters["angle_degrees"])),
        tuple(cast(list[int], parameters["resolution"])),
        float(cast(float, parameters["duration"])),
        float(cast(float, parameters["epsilon"])),
    )
    if observed_values != expected_values:
        failures.append(
            f"{language}: sensitivity identity {observed_values!r} != {expected_values!r}"
        )
    requested = float(cast(float, initialization["requested_epsilon"]))
    realized = float(
        cast(float, initialization["realized_post_import_wake_rms_difference"])
    )
    metric_realized = float(cast(float, metrics["initial_wake_rms_difference"]))
    ratio = float(cast(float, initialization["realized_to_requested_ratio"]))
    authoritative_angle = float(cast(float, initialization["authoritative_angle_degrees"]))
    if not all(math.isfinite(value) and value > 0.0 for value in (requested, realized, ratio)):
        failures.append(f"{language}: sensitivity initialization is not finite and positive")
        return
    if not math.isclose(requested, expected_values[4], rel_tol=1.0e-12, abs_tol=0.0):
        failures.append(f"{language}: requested epsilon does not match the fixture")
    if not math.isclose(realized, metric_realized, rel_tol=1.0e-12, abs_tol=0.0):
        failures.append(f"{language}: realized separation disagrees with the sensitivity metric")
    if not math.isclose(ratio, realized / requested, rel_tol=1.0e-9, abs_tol=0.0):
        failures.append(f"{language}: realized/requested ratio is internally inconsistent")
    if not math.isclose(authoritative_angle, expected_values[1], rel_tol=0.0, abs_tol=1.0e-6):
        failures.append(f"{language}: sensitivity initialization used the wrong foil pose")
    minimum = float(cast(float, ratio_bounds["minimum_realized_to_requested_ratio"]))
    maximum = float(cast(float, ratio_bounds["maximum_realized_to_requested_ratio"]))
    if not minimum <= ratio <= maximum:
        failures.append(
            f"{language}: realized/requested ratio {ratio:.6g} is outside "
            f"[{minimum:.6g}, {maximum:.6g}]"
        )


def validate_chaos_acceptance(
    paths: Sequence[Path], *, required_languages: Sequence[str] = ()
) -> str:
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
    thresholds = cast(Mapping[str, object], acceptance["chaotic_extension"])
    cases = cast(
        dict[str, object],
        json.loads(
            (root / str(thresholds["fixture"])).read_text(encoding="utf-8")
        ),
    )
    schema = cast(
        dict[str, object],
        json.loads(
            (root / "spec/chaotic-wake-result.schema.json").read_text(encoding="utf-8")
        ),
    )
    expected_cases = {
        (
            float(cast(float, entry["reynolds"])),
            float(cast(float, entry["angle_degrees"])),
            tuple(cast(list[int], entry["resolution"])),
        )
        for entry in cast(list[dict[str, object]], cast(dict[str, object], cases["sweep"])["cases"])
    }
    sensitivity_fixture = cast(Mapping[str, object], cases["sensitivity"])
    preflight_fixture = cast(Mapping[str, object], cases["initialization_preflight"])
    documents = _documents(paths)
    grouped: dict[str, list[dict[str, object]]] = {}
    for document in documents:
        validate_json(document, schema)
        grouped.setdefault(str(document["language"]), []).append(document)
    failures: list[str] = []
    if required_languages:
        expected_languages = set(required_languages)
        if not expected_languages or len(expected_languages) != len(required_languages):
            raise ValueError("required languages must be a non-empty unique roster")
        observed_languages = set(grouped)
        if observed_languages != expected_languages:
            failures.append(
                "chaotic-wake producer roster mismatch "
                f"missing={sorted(expected_languages - observed_languages)!r} "
                f"extra={sorted(observed_languages - expected_languages)!r}"
            )
    for language, entries in grouped.items():
        observed_cases: set[tuple[float, float, tuple[int, ...]]] = set()
        sensitivity_count = 0
        for entry in entries:
            if entry["experiment"] == "chaotic-wake-sensitivity":
                sensitivity_count += 1
                _check_sensitivity_initialization(
                    entry, sensitivity_fixture, preflight_fixture, failures
                )
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


def validate_chaos_preflight(
    paths: Sequence[Path], *, required_languages: Sequence[str] = ()
) -> str:
    """Validate short full-resolution paired-initialization artifacts."""

    if not paths:
        raise ValueError("at least one chaotic-wake preflight artifact is required")
    root = find_repo_root(Path(__file__))
    cases = cast(
        dict[str, object],
        json.loads(
            (root / "spec/conformance/chaotic-wake-cases.json").read_text(
                encoding="utf-8"
            )
        ),
    )
    preflight = cast(Mapping[str, object], cases["initialization_preflight"])
    schema = cast(
        dict[str, object],
        json.loads(
            (root / "spec/chaotic-wake-result.schema.json").read_text(encoding="utf-8")
        ),
    )
    grouped: dict[str, list[dict[str, object]]] = {}
    failures: list[str] = []
    for document in _documents(paths):
        validate_json(document, schema)
        language = str(document["language"])
        grouped.setdefault(language, []).append(document)
        if document["experiment"] != "chaotic-wake-sensitivity":
            failures.append(f"{language}: preflight is not a sensitivity artifact")
            continue
        _check_sensitivity_initialization(document, preflight, preflight, failures)
    if required_languages:
        expected_languages = set(required_languages)
        if not expected_languages or len(expected_languages) != len(required_languages):
            raise ValueError("required languages must be a non-empty unique roster")
        observed_languages = set(grouped)
        if observed_languages != expected_languages:
            failures.append(
                "chaotic-wake preflight producer roster mismatch "
                f"missing={sorted(expected_languages - observed_languages)!r} "
                f"extra={sorted(observed_languages - expected_languages)!r}"
            )
    for language, entries in grouped.items():
        if len(entries) != 1:
            failures.append(f"{language}: expected one preflight artifact, found {len(entries)}")
    if failures:
        raise ValueError("chaotic-wake preflight failed:\n" + "\n".join(failures))
    return f"Chaotic-wake preflight passed for {', '.join(sorted(grouped))}."
