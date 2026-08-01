"""Regenerate language-neutral FoilBench conformance fixtures."""

import json
from pathlib import Path

import numpy as np
from foilbench_py.core.geometry import NacaFoil
from foilbench_py.core.models import CanonicalFlowState, FoilSpec
from foilbench_py.core.rng import PCG32
from foilbench_py.core.state_io import save_canonical_state

ROOT = Path(__file__).resolve().parents[1]
DESTINATION = ROOT / "spec" / "conformance"


def _write_json(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, indent=2) + "\n", encoding="utf-8")


def _pcg_case(seed: int, stream: int, count: int) -> dict[str, object]:
    integers = PCG32(seed, stream=stream)
    uint32 = [integers.next_uint32() for _ in range(count)]
    floats = PCG32(seed, stream=stream).random((count,))
    float32_bits = [f"0x{int(value):08x}" for value in floats.view(np.uint32)]
    return {
        "seed": seed,
        "stream": stream,
        "uint32": uint32,
        "float32_bits": float32_bits,
    }


def generate_pcg32() -> None:
    _write_json(
        DESTINATION / "pcg32.json",
        {
            "schema_version": 1,
            "algorithm": "PCG-XSH-RR 64/32",
            "multiplier_uint64": "6364136223846793005",
            "float_conversion": "Float32(uint32) * Float32(2^-32)",
            "cases": [
                _pcg_case(42, 54, 12),
                _pcg_case(0, 54, 12),
                _pcg_case(7, 3, 12),
            ],
        },
    )


def generate_geometry() -> None:
    spec = FoilSpec(naca="2412", chord=1.0, pivot=(0.1, -0.2))
    foil = NacaFoil(spec)
    surface_x = np.asarray([0.0, 0.025, 0.1, 0.2, 0.4, 0.7, 1.0], dtype=np.float64)
    upper, lower = foil.surfaces(surface_x)
    points = np.asarray(
        [
            [-0.3, -0.2],
            [-0.1, -0.2],
            [0.1, -0.2],
            [0.35, -0.2],
            [0.6, -0.1],
            [0.85, 0.0],
            [1.05, 0.1],
            [0.2, 0.35],
        ],
        dtype=np.float64,
    )
    queries: list[dict[str, object]] = []
    for angle_degrees in (0.0, 17.5, -31.0):
        signed_distance = foil.signed_distance(points, angle_degrees)
        queries.append(
            {
                "angle_degrees": angle_degrees,
                "points": points.tolist(),
                "signed_distance": signed_distance.tolist(),
                "normals": foil.normals(points, angle_degrees).tolist(),
                "contains": (signed_distance <= 0.0).tolist(),
            }
        )
    _write_json(
        DESTINATION / "naca2412.json",
        {
            "schema_version": 1,
            "foil": {"naca": spec.naca, "chord": spec.chord, "pivot": list(spec.pivot)},
            "surface_x": surface_x.tolist(),
            "surface_upper": upper.tolist(),
            "surface_lower": lower.tolist(),
            "queries": queries,
            "absolute_tolerances": {
                "surface": 1.0e-12,
                "signed_distance": 1.0e-10,
                "normal": 2.0e-6,
            },
        },
    )


def generate_canonical_state() -> None:
    velocity = (
        np.arange(1 * 3 * 4 * 2, dtype=np.float32).reshape(1, 3, 4, 2) - 8.0
    ) / np.float32(7.0)
    density = np.linspace(0.9, 1.1, 12, dtype=np.float32).reshape(1, 3, 4)
    state = CanonicalFlowState(
        schema_version=1,
        dimension=2,
        bounds=((-1.0, 1.0), (-0.75, 0.75)),
        resolution=(4, 3),
        periodic_axes=("x",),
        time=1.25,
        precision="float32",
        angle_degrees=17.5,
        angular_velocity_degrees=-2.25,
        source_language="conformance",
        source_solver="golden",
        velocity=velocity,
        density=density,
    )
    save_canonical_state(state, DESTINATION / "canonical-state-f32")


def main() -> None:
    generate_pcg32()
    generate_geometry()
    generate_canonical_state()


if __name__ == "__main__":
    main()
