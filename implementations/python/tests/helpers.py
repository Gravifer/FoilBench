from collections.abc import Callable

from foilbench_py.core.models import (
    AxisName,
    ControlKeyframe,
    DomainSpec,
    FoilSpec,
    Scenario,
)

type ScenarioFactory = Callable[..., Scenario]


def make_scenario(
    *,
    resolution: tuple[int, int] = (48, 24),
    initial_condition: str = "freestream",
    foil_in_domain: bool = True,
    periodic_axes: tuple[AxisName, ...] = (),
) -> Scenario:
    pivot = (0.0, 0.0) if foil_in_domain else (-10.0, -10.0)
    return Scenario(
        schema_version=1,
        id=f"test-{initial_condition}",
        domain=DomainSpec(
            dimension=2,
            bounds=((-2.0, 6.0), (-2.0, 2.0)),
            resolution=resolution,
            periodic_axes=periodic_axes,
        ),
        reynolds=500.0,
        freestream=(1.0, 0.0),
        foil=FoilSpec(naca="2412", chord=1.0, pivot=pivot),
        controls=(
            ControlKeyframe(0.0, 4.0),
            ControlKeyframe(1.0, 20.0),
        ),
        duration=0.05,
        output_dt=0.01,
        precision="float32",
        seed=0,
        solver_options={
            "initial_condition": initial_condition,
            "pic_flip_blend": 0.95,
            "stable_advection": "maccormack",
        },
    )
