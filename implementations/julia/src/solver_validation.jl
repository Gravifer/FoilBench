function precision_tolerance(::Type{T}, values::Real...) where {T<:AbstractFloat}
    base = T === Float32 ? 1.0e-6 : 1.0e-12
    scale = isempty(values) ? 1.0 : max(1.0, maximum(abs, values))
    return base * scale
end

function validate_advance_request(
    current_time::T,
    control::ControlState,
    target_dt::Real,
) where {T<:AbstractFloat}
    isfinite(target_dt) && target_dt > 0 ||
        throw(ArgumentError("target_dt must be finite and positive"))
    all(isfinite, (control.time, control.angle_degrees, control.angular_velocity_degrees)) ||
        throw(NumericalFailure(
            :time_contract_failure,
            "control state must be finite",
            Symbol("time-mapping"),
        ))
    target = T(target_dt)
    expected = current_time + target
    tolerance = precision_tolerance(T, current_time, expected, control.time)
    abs(control.time - expected) <= tolerance || throw(NumericalFailure(
        :time_contract_failure,
        "control completion time disagrees with the requested interval",
        Symbol("time-mapping"),
        Dict{String,Any}(
            "expected_time" => expected,
            "control_time" => control.time,
            "target_dt" => target,
        ),
    ))
    return target
end

function validate_restart_state(start::RestartState)
    all(isfinite, (start.time, start.angle_degrees, start.reynolds)) ||
        throw(ArgumentError("restart state must be finite"))
    start.time >= 0 || throw(ArgumentError("restart time must be nonnegative"))
    start.reynolds > 0 || throw(ArgumentError("restart Reynolds number must be positive"))
    return nothing
end

function validate_canonical_import(
    state::CanonicalFlowState{StateD,S},
    scenario::Scenario{ScenarioD,T},
    control::ControlState,
) where {StateD,ScenarioD,S,T<:AbstractFloat}
    state.schema_version in (1, 2) || throw(NumericalFailure(
        :incompatible_domain,
        "canonical schema version is unsupported",
        Symbol("canonical-import"),
    ))
    StateD == ScenarioD || throw(NumericalFailure(
        :incompatible_domain,
        "canonical dimension does not match the scenario",
        Symbol("canonical-import"),
    ))
    state.resolution == scenario.domain.resolution || throw(NumericalFailure(
        :incompatible_domain,
        "canonical resolution does not match the scenario",
        Symbol("canonical-import"),
    ))
    state.periodic_axes == scenario.domain.periodic_axes || throw(NumericalFailure(
        :incompatible_domain,
        "canonical periodic axes do not match the scenario",
        Symbol("canonical-import"),
    ))
    S === T || throw(NumericalFailure(
        :incompatible_domain,
        "canonical precision does not match the scenario",
        Symbol("canonical-import"),
    ))
    expected_z = StateD == 2 ? 1 : state.resolution[3]
    expected_velocity = (expected_z, state.resolution[2], state.resolution[1], StateD)
    size(state.velocity) == expected_velocity &&
        (state.density === nothing || size(state.density) == expected_velocity[1:3]) ||
        throw(NumericalFailure(
            :incompatible_domain,
            "canonical array shape does not match metadata",
            Symbol("canonical-import"),
        ))
    all(isfinite, state.velocity) &&
        (state.density === nothing || all(isfinite, state.density)) ||
        throw(NumericalFailure(
            :nonfinite_state,
            "canonical arrays must be finite at import time",
            Symbol("canonical-import"),
        ))
    tolerance = precision_tolerance(T, Iterators.flatten(state.bounds)..., Iterators.flatten(scenario.domain.bounds)...)
    all(
        abs(state.bounds[axis][side] - scenario.domain.bounds[axis][side]) <= tolerance
        for axis in 1:StateD for side in 1:2
    ) || throw(NumericalFailure(
        :incompatible_domain,
        "canonical bounds do not match the scenario",
        Symbol("canonical-import"),
    ))
    all(isfinite, (control.time, control.angle_degrees, control.angular_velocity_degrees)) ||
        throw(NumericalFailure(
            :time_contract_failure,
            "import control must be finite",
            Symbol("canonical-import"),
        ))
    control_tolerance = precision_tolerance(
        T,
        state.time,
        control.time,
        state.angle_degrees,
        control.angle_degrees,
        state.angular_velocity_degrees,
        control.angular_velocity_degrees,
    )
    (
        abs(state.time - control.time) <= control_tolerance &&
        abs(state.angle_degrees - control.angle_degrees) <= control_tolerance &&
        abs(state.angular_velocity_degrees - control.angular_velocity_degrees) <= control_tolerance
    ) || throw(NumericalFailure(
        :time_contract_failure,
        "canonical time or foil control disagrees with the import control",
        Symbol("canonical-import"),
        Dict{String,Any}(
            "state_time" => state.time,
            "control_time" => control.time,
            "state_angle" => state.angle_degrees,
            "control_angle" => control.angle_degrees,
        ),
    ))
    state.schema_version == 1 || state.geometry == scenario.foil || throw(NumericalFailure(
        :incompatible_geometry,
        "canonical geometry does not match the scenario",
        Symbol("canonical-import"),
    ))
    if StateD == 2
        geometry = NacaFoil(scenario.foil)
        solid = solid_mask(geometry, scenario.domain, control.angle_degrees)
        nonzero = 0
        for j in 1:state.resolution[2], i in 1:state.resolution[1]
            solid[i, j] || continue
            any(state.velocity[1, j, i, component] != zero(S) for component in 1:StateD) &&
                (nonzero += 1)
        end
        nonzero == 0 || throw(NumericalFailure(
            :postcondition_failure,
            "canonical solid-cell velocity must be exactly zero",
            Symbol("canonical-import"),
            Dict{String,Any}("nonzero_solid_cells" => nonzero),
        ))
    end
    return nothing
end
