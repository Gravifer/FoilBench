"""Shared validation for transactional solver entry points."""

import math

import numpy as np

from foilbench_py.core.geometry import NacaFoil
from foilbench_py.core.models import (
    CanonicalFlowState,
    ControlState,
    NumericalFailure,
    RestartState,
    Scenario,
)


def precision_tolerance(precision: str, *values: float) -> float:
    base = 1.0e-6 if precision == "float32" else 1.0e-12
    return base * max(1.0, *(abs(value) for value in values))


def validate_advance_request(
    current_time: float,
    control: ControlState,
    target_dt: float,
    precision: str,
) -> None:
    if not math.isfinite(target_dt) or target_dt <= 0.0:
        raise ValueError("target_dt must be finite and positive")
    if not all(
        math.isfinite(value)
        for value in (
            control.time,
            control.angle_degrees,
            control.angular_velocity_degrees,
        )
    ):
        raise NumericalFailure(
            "time_contract_failure",
            "control state must be finite",
            "time-mapping",
        )
    expected = current_time + target_dt
    tolerance = precision_tolerance(precision, current_time, expected, control.time)
    if abs(control.time - expected) > tolerance:
        raise NumericalFailure(
            "time_contract_failure",
            "control completion time disagrees with the requested interval",
            "time-mapping",
            {
                "expected_time": expected,
                "control_time": control.time,
                "target_dt": target_dt,
            },
        )


def validate_restart_state(start: RestartState) -> None:
    if not all(
        math.isfinite(value)
        for value in (start.time, start.angle_degrees, start.reynolds)
    ):
        raise ValueError("restart state must be finite")
    if start.time < 0.0 or start.reynolds <= 0.0:
        raise ValueError("restart time must be nonnegative and Reynolds positive")


def validate_canonical_import(
    state: CanonicalFlowState,
    scenario: Scenario,
    control: ControlState,
) -> None:
    if state.schema_version != 1:
        raise NumericalFailure(
            "incompatible_domain",
            "canonical schema version is unsupported",
            "canonical-import",
        )
    if state.dimension != scenario.domain.dimension:
        raise NumericalFailure(
            "incompatible_domain",
            "canonical dimension does not match the scenario",
            "canonical-import",
        )
    if state.resolution != scenario.domain.resolution:
        raise NumericalFailure(
            "incompatible_domain",
            "canonical resolution does not match the scenario",
            "canonical-import",
        )
    if state.periodic_axes != scenario.domain.periodic_axes:
        raise NumericalFailure(
            "incompatible_domain",
            "canonical periodic axes do not match the scenario",
            "canonical-import",
        )
    if state.precision != scenario.precision:
        raise NumericalFailure(
            "incompatible_domain",
            "canonical precision does not match the scenario",
            "canonical-import",
        )
    expected_z = 1 if state.dimension == 2 else state.resolution[2]
    expected_velocity_shape = (
        expected_z,
        state.resolution[1],
        state.resolution[0],
        state.dimension,
    )
    expected_density_shape = expected_velocity_shape[:-1]
    expected_dtype = np.dtype(state.precision)
    if (
        state.velocity.shape != expected_velocity_shape
        or state.velocity.dtype != expected_dtype
        or (
            state.density is not None
            and (
                state.density.shape != expected_density_shape
                or state.density.dtype != expected_dtype
            )
        )
    ):
        raise NumericalFailure(
            "incompatible_domain",
            "canonical array shape or dtype does not match metadata",
            "canonical-import",
        )
    if not np.isfinite(state.velocity).all() or (
        state.density is not None and not np.isfinite(state.density).all()
    ):
        raise NumericalFailure(
            "nonfinite_state",
            "canonical arrays must be finite at import time",
            "canonical-import",
        )
    state_bounds = np.asarray(state.bounds, dtype=np.float64)
    scenario_bounds = np.asarray(scenario.domain.bounds, dtype=np.float64)
    tolerance = precision_tolerance(
        scenario.precision,
        *state_bounds.reshape(-1).tolist(),
        *scenario_bounds.reshape(-1).tolist(),
    )
    if not np.allclose(state_bounds, scenario_bounds, rtol=0.0, atol=tolerance):
        raise NumericalFailure(
            "incompatible_domain",
            "canonical bounds do not match the scenario",
            "canonical-import",
        )
    if not all(
        math.isfinite(value)
        for value in (
            control.time,
            control.angle_degrees,
            control.angular_velocity_degrees,
        )
    ):
        raise NumericalFailure(
            "time_contract_failure",
            "import control must be finite",
            "canonical-import",
        )
    control_tolerance = precision_tolerance(
        scenario.precision,
        state.time,
        control.time,
        state.angle_degrees,
        control.angle_degrees,
        state.angular_velocity_degrees,
        control.angular_velocity_degrees,
    )
    if (
        abs(state.time - control.time) > control_tolerance
        or abs(state.angle_degrees - control.angle_degrees) > control_tolerance
        or abs(state.angular_velocity_degrees - control.angular_velocity_degrees)
        > control_tolerance
    ):
        raise NumericalFailure(
            "time_contract_failure",
            "canonical time or foil control disagrees with the import control",
            "canonical-import",
            {
                "state_time": state.time,
                "control_time": control.time,
                "state_angle": state.angle_degrees,
                "control_angle": control.angle_degrees,
            },
        )
    if state.dimension == 2:
        solid = NacaFoil(scenario.foil).mask(
            scenario.domain,
            control.angle_degrees,
        )
        if np.any(state.velocity[0][solid] != 0.0):
            nonzero_solid_cells = int(
                np.count_nonzero(
                    np.any(state.velocity[0][solid] != 0.0, axis=1)
                )
            )
            raise NumericalFailure(
                "postcondition_failure",
                "canonical solid-cell velocity must be exactly zero",
                "canonical-import",
                {"nonzero_solid_cells": nonzero_solid_cells},
            )
