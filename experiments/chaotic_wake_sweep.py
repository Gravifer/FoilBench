"""Long-horizon diagnostics for provisional 2D chaotic-wake experiments."""

import argparse
import itertools
import json
import struct
import time
import zlib
from dataclasses import asdict, dataclass, replace
from pathlib import Path

import numpy as np
from foilbench_py.core._schema_adapter import validate_json
from foilbench_py.core.geometry import NacaFoil
from foilbench_py.core.metrics import vorticity
from foilbench_py.core.models import ControlKeyframe, Scenario
from foilbench_py.core.scenario import find_repo_root, load_scenario
from foilbench_py.solvers.factory import create_solver


@dataclass(frozen=True, slots=True)
class SweepCase:
    reynolds: float
    angle_degrees: float
    resolution: tuple[int, int]


@dataclass(frozen=True, slots=True)
class WakeStatistics:
    reynolds: float
    angle_degrees: float
    resolution: tuple[int, int]
    duration: float
    analysis_duration: float
    wall_seconds: float
    probe_rms: float
    spectral_entropy: float
    dominant_power_fraction: float
    broadband_power_fraction: float
    decorrelation_time: float
    enstrophy_mean: float
    enstrophy_coefficient_of_variation: float
    maximum_speed: float
    vorticity_high_wavenumber_fraction: float
    vorticity_spectral_slope: float
    vorticity_small_scale_fraction: float


def _spectral_statistics(samples: np.ndarray) -> tuple[float, float, float]:
    centered = np.asarray(samples - np.mean(samples), dtype=np.float64)
    windowed = centered * np.hanning(centered.size)
    power = np.abs(np.fft.rfft(windowed)) ** 2
    power[0] = 0.0
    total = float(np.sum(power))
    if total <= np.finfo(np.float64).tiny:
        return 0.0, 0.0, 0.0
    probability = power / total
    active = probability > 0.0
    entropy = -float(np.sum(probability[active] * np.log(probability[active])))
    entropy /= np.log(max(2, probability.size - 1))
    dominant = int(np.argmax(power))
    coherent_start = max(1, dominant - 1)
    coherent_stop = min(power.size, dominant + 2)
    coherent = float(np.sum(power[coherent_start:coherent_stop]) / total)
    return entropy, float(power[dominant] / total), 1.0 - coherent


def _decorrelation_time(samples: np.ndarray, sample_dt: float) -> float:
    centered = np.asarray(samples - np.mean(samples), dtype=np.float64)
    variance = float(np.dot(centered, centered))
    if variance <= np.finfo(np.float64).tiny:
        return 0.0
    correlation = np.correlate(centered, centered, mode="full")[centered.size - 1 :]
    overlap = np.arange(centered.size, 0, -1, dtype=np.float64)
    correlation = correlation / overlap
    correlation /= correlation[0]
    below = np.flatnonzero(correlation < np.exp(-1.0))
    return float((below[0] if below.size else centered.size - 1) * sample_dt)


def _spatial_vorticity_statistics(
    velocity: np.ndarray,
    scenario: Scenario,
) -> tuple[float, float, np.ndarray]:
    omega = np.asarray(vorticity(velocity, scenario.domain), dtype=np.float64)
    x0 = scenario.domain.bounds[0][0]
    wake_start = max(
        0,
        int((scenario.foil.pivot[0] + 0.5 * scenario.foil.chord - x0) / scenario.domain.dx),
    )
    wake = omega[:, wake_start:]
    window = np.hanning(wake.shape[0])[:, None] * np.hanning(wake.shape[1])[None, :]
    power = np.abs(np.fft.rfft2((wake - np.mean(wake)) * window)) ** 2
    ky = np.fft.fftfreq(wake.shape[0]) * wake.shape[0]
    kx = np.fft.rfftfreq(wake.shape[1]) * wake.shape[1]
    radius = np.rint(np.sqrt(ky[:, None] ** 2 + kx[None, :] ** 2)).astype(np.int64)
    radial = np.bincount(radius.ravel(), weights=power.ravel())
    radial[0] = 0.0
    total = float(np.sum(radial))
    high_start = max(2, int(2 * radial.size / 3))
    high_fraction = float(np.sum(radial[high_start:]) / max(total, 1.0e-30))
    upper_fit = max(5, radial.size // 3)
    wave_numbers = np.arange(2, upper_fit, dtype=np.float64)
    selected_power = radial[2:upper_fit]
    valid = selected_power > 0.0
    slope = (
        float(np.polyfit(np.log(wave_numbers[valid]), np.log(selected_power[valid]), 1)[0])
        if np.count_nonzero(valid) >= 4
        else 0.0
    )
    return high_fraction, slope, omega


def _png_chunk(kind: bytes, payload: bytes) -> bytes:
    return (
        struct.pack(">I", len(payload))
        + kind
        + payload
        + struct.pack(">I", zlib.crc32(kind + payload) & 0xFFFFFFFF)
    )


def _write_vorticity_png(path: Path, omega: np.ndarray) -> None:
    scale = max(float(np.percentile(np.abs(omega), 99.0)), 1.0e-12)
    normalized = np.clip(omega / scale, -1.0, 1.0)
    red = np.asarray(255.0 * np.maximum(normalized, 0.0), dtype=np.uint8)
    blue = np.asarray(255.0 * np.maximum(-normalized, 0.0), dtype=np.uint8)
    green = np.asarray(40.0 * np.abs(normalized), dtype=np.uint8)
    rgb = np.stack((red, green, blue), axis=2)[::-1]
    scanlines = b"".join(b"\x00" + row.tobytes() for row in rgb)
    header = struct.pack(">IIBBBBB", rgb.shape[1], rgb.shape[0], 8, 2, 0, 0, 0)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(
        b"\x89PNG\r\n\x1a\n"
        + _png_chunk(b"IHDR", header)
        + _png_chunk(b"IDAT", zlib.compress(scanlines, level=9))
        + _png_chunk(b"IEND", b"")
    )


def _scenario(base: Scenario, case: SweepCase, duration: float) -> Scenario:
    return replace(
        base,
        id=(
            f"chaotic-wake-re{case.reynolds:g}-a{case.angle_degrees:g}-"
            f"{case.resolution[0]}x{case.resolution[1]}"
        ),
        domain=replace(base.domain, resolution=case.resolution),
        reynolds=case.reynolds,
        controls=(
            ControlKeyframe(0.0, case.angle_degrees),
            ControlKeyframe(duration, case.angle_degrees),
        ),
        duration=duration,
        solver_options={
            **base.solver_options,
            "stable_face_advection": True,
        },
    )


def run_case(
    base: Scenario,
    case: SweepCase,
    duration: float,
    burn_in: float,
    image_output: Path | None,
) -> WakeStatistics:
    scenario = _scenario(base, case, duration)
    solver = create_solver("stable-fluids")
    solver.initialize(scenario, NacaFoil(scenario.foil), scenario.seed)
    probe = np.asarray(
        [[scenario.foil.pivot[0] + 1.5 * scenario.foil.chord, scenario.foil.pivot[1]]],
        dtype=scenario.dtype,
    )
    transverse: list[float] = []
    enstrophy: list[float] = []
    maximum_speed = 0.0
    simulated = 0.0
    started = time.perf_counter()
    while simulated < duration - 1.0e-12:
        dt = min(scenario.output_dt, duration - simulated)
        simulated += dt
        report = solver.advance(scenario.control_at(simulated), dt)
        maximum_speed = max(maximum_speed, report.max_speed)
        if simulated >= burn_in:
            transverse.append(float(solver.sample_velocity(probe)[0, 1]))
            enstrophy.append(solver.diagnostics().values["enstrophy"])
    wall_seconds = time.perf_counter() - started
    transverse_array = np.asarray(transverse, dtype=np.float64)
    enstrophy_array = np.asarray(enstrophy, dtype=np.float64)
    entropy, dominant, broadband = _spectral_statistics(transverse_array)
    enstrophy_mean = float(np.mean(enstrophy_array))
    high_wavenumber, spatial_slope, omega = _spatial_vorticity_statistics(
        solver.export_state().velocity[0], scenario
    )
    omega_gradient = np.gradient(omega)
    small_scale_fraction = float(
        sum(float(np.sum(component * component)) for component in omega_gradient)
        / max(float(np.sum(omega * omega)), np.finfo(np.float64).tiny)
    )
    if image_output is not None:
        _write_vorticity_png(image_output, omega)
    return WakeStatistics(
        reynolds=case.reynolds,
        angle_degrees=case.angle_degrees,
        resolution=case.resolution,
        duration=duration,
        analysis_duration=duration - burn_in,
        wall_seconds=wall_seconds,
        probe_rms=float(np.std(transverse_array)),
        spectral_entropy=entropy,
        dominant_power_fraction=dominant,
        broadband_power_fraction=broadband,
        decorrelation_time=_decorrelation_time(transverse_array, scenario.output_dt),
        enstrophy_mean=enstrophy_mean,
        enstrophy_coefficient_of_variation=(
            float(np.std(enstrophy_array)) / max(enstrophy_mean, 1.0e-12)
        ),
        maximum_speed=maximum_speed,
        vorticity_high_wavenumber_fraction=high_wavenumber,
        vorticity_spectral_slope=spatial_slope,
        vorticity_small_scale_fraction=small_scale_fraction,
    )


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--scenario",
        type=Path,
        default=Path("scenarios/airfoil/chaotic-experimental.json"),
    )
    parser.add_argument(
        "--single",
        nargs=4,
        type=float,
        metavar=("RE", "ANGLE", "NX", "NY"),
    )
    parser.add_argument("--duration", type=float, default=12.0)
    parser.add_argument("--burn-in", type=float, default=4.0)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--image", type=Path)
    parser.add_argument("--scheme", choices=("maccormack", "skew-rk2"), default="skew-rk2")
    return parser


def main() -> None:
    arguments = _parser().parse_args()
    root = find_repo_root(Path(__file__))
    base = load_scenario(root / arguments.scenario)
    if arguments.single is None:
        cases = tuple(
            SweepCase(reynolds, angle, (160, 96))
            for reynolds, angle in itertools.product((1_000.0, 10_000.0), (25.0, 35.0))
        )
    else:
        reynolds, angle, nx, ny = arguments.single
        cases = (SweepCase(reynolds, angle, (int(nx), int(ny))),)
    if arguments.scheme == "skew-rk2":
        base = replace(
            base,
            solver_options={**base.solver_options, "stable_advection": "skew-rk2"},
        )
    result_schema = json.loads(
        (root / "spec" / "schemas" / "chaotic-wake-result.schema.json").read_text(encoding="utf-8")
    )
    results: list[dict[str, object]] = []
    for case in cases:
        statistics = run_case(
            base,
            case,
            arguments.duration,
            arguments.burn_in,
            arguments.image,
        )
        raw = asdict(statistics)
        result: dict[str, object] = {
            "schema_version": 1,
            "contract_id": "foilbench-phase2-v1",
            "contract_revision": 4,
            "experiment": "chaotic-wake-sweep",
            "language": "python",
            "solver": "stable-fluids",
            "scenario": _scenario(base, case, arguments.duration).id,
            "parameters": {
                "reynolds": case.reynolds,
                "angle_degrees": case.angle_degrees,
                "resolution": list(case.resolution),
                "duration": arguments.duration,
                "burn_in": arguments.burn_in,
            },
            "metrics": {
                key: raw[key]
                for key in (
                    "probe_rms",
                    "spectral_entropy",
                    "dominant_power_fraction",
                    "broadband_power_fraction",
                    "decorrelation_time",
                    "enstrophy_mean",
                    "enstrophy_coefficient_of_variation",
                    "maximum_speed",
                    "vorticity_small_scale_fraction",
                )
            },
            "wall_seconds": statistics.wall_seconds,
        }
        validate_json(result, result_schema)
        results.append(result)
        print(json.dumps(result))
    if arguments.output is not None:
        arguments.output.parent.mkdir(parents=True, exist_ok=True)
        arguments.output.write_text(json.dumps(results, indent=2), encoding="utf-8")


if __name__ == "__main__":
    main()
