mutable struct TracerState{T<:AbstractFloat}
    positions::Matrix{T}
    history::Array{T,3}
    history_cursor::Int
    ages::Vector{T}
    lifetimes::Vector{T}
    generations::Vector{UInt64}
    history_generations::Matrix{UInt64}
    mode::Symbol
    rng::PCG32
    recycle_counters::Dict{Symbol,Int}
end

struct ViewerSnapshot{T<:AbstractFloat}
    revision::UInt64
    applied_command::UInt64
    solver_epoch::Int
    solver_state_revision::Int
    diagnostic_solver_state_revision::Union{Nothing,Int}
    vorticity_solver_state_revision::Union{Nothing,Int}
    time::T
    angle_degrees::T
    solver_id::String
    tracer_positions::Matrix{T}
    path_segments::Matrix{T}
    vorticity::Matrix{T}
    diagnostics::Dict{String,Float64}
    status::String
    paused::Bool
    vorticity_visible::Bool
    crop_enabled::Bool
    tracer_mode::Symbol
    phase::Symbol
    motion_mode::Symbol
    diagnostic_mode::Symbol
    schedule_active::Bool
    recovery_epoch::Int
    tracer_recycle_counters::Dict{Symbol,Int}
end

_random_fraction(rng::PCG32, ::Type{T}) where {T<:AbstractFloat} = T(next_float32!(rng))

function _seed_position!(
    positions::AbstractMatrix{T},
    index::Int,
    rng::PCG32,
    scenario::Scenario{2,T},
    geometry::NacaFoil{2,T},
    angle_degrees::T,
    placement::Symbol,
) where {T}
    placement in (:domain, :inlet) || throw(ArgumentError("unknown tracer placement"))
    x0, x1 = scenario.domain.bounds[1]
    y0, y1 = scenario.domain.bounds[2]
    for _ in 1:64
        x = placement == :inlet ?
            x0 + T(0.5) * _random_fraction(rng, T) * dx(scenario.domain) :
            x0 + _random_fraction(rng, T) * (x1 - x0)
        y = y0 + _random_fraction(rng, T) * (y1 - y0)
        signed_distance(geometry, SVector{2,T}(x, y), angle_degrees) > zero(T) || continue
        positions[1, index] = x
        positions[2, index] = y
        return nothing
    end
    positions[1, index] = x0 + T(0.5) * dx(scenario.domain)
    positions[2, index] = y0 + T(0.5) * (y1 - y0)
    return nothing
end

function TracerState(
    scenario::Scenario{2,T},
    geometry::NacaFoil{2,T},
    angle_degrees::T;
    count::Int,
    history_length::Int = 12,
    mode::Symbol = :display,
) where {T}
    count > 0 || throw(ArgumentError("tracer count must be positive"))
    history_length >= 2 || throw(ArgumentError("tracer history must contain at least two points"))
    mode in (:display, :material) || throw(ArgumentError("unknown tracer mode"))
    rng = PCG32(scenario.seed, 71)
    positions = Matrix{T}(undef, 2, count)
    for index in 1:count
        _seed_position!(positions, index, rng, scenario, geometry, angle_degrees, :domain)
    end
    history = Array{T,3}(undef, 2, count, history_length)
    for history_index in 1:history_length
        history[:, :, history_index] = positions
    end
    lifetimes = [T(3) + T(4) * _random_fraction(rng, T) for _ in 1:count]
    ages = [_random_fraction(rng, T) * lifetimes[index] for index in 1:count]
    generations = zeros(UInt64, count)
    history_generations = zeros(UInt64, count, history_length)
    return TracerState(
        positions,
        history,
        history_length,
        ages,
        lifetimes,
        generations,
        history_generations,
        mode,
        rng,
        Dict(
            :boundary_exit => 0,
            :lifetime_expiry => 0,
            :invalid_collision => 0,
            :forced_recovery => 0,
            :scenario_reset => 0,
            :periodic_wrap => 0,
        ),
    )
end

function _reset_tracer_history!(tracers::TracerState, index::Int)
    for history_index in axes(tracers.history, 3)
        tracers.history[:, index, history_index] = tracers.positions[:, index]
        tracers.history_generations[index, history_index] = tracers.generations[index]
    end
    return nothing
end

function _respawn_tracer!(
    tracers::TracerState{T},
    index::Int,
    scenario::Scenario{2,T},
    geometry::NacaFoil{2,T},
    angle_degrees::T,
    placement::Symbol,
    reason::Union{Nothing,Symbol} = nothing,
) where {T}
    _seed_position!(
        tracers.positions,
        index,
        tracers.rng,
        scenario,
        geometry,
        angle_degrees,
        placement,
    )
    tracers.generations[index] += 1
    reason === nothing || (tracers.recycle_counters[reason] += 1)
    _reset_tracer_lifetime!(tracers, index)
    _reset_tracer_history!(tracers, index)
    return nothing
end

function _outside_domain(position::AbstractVector{T}, domain::DomainSpec{2,T}) where {T}
    return position[1] < domain.bounds[1][1] || position[1] > domain.bounds[1][2] ||
        position[2] < domain.bounds[2][1] || position[2] > domain.bounds[2][2]
end

function _reset_tracer_lifetime!(tracers::TracerState{T}, index::Int) where {T}
    tracers.ages[index] = zero(T)
    tracers.lifetimes[index] = T(3) + T(4) * _random_fraction(tracers.rng, T)
    return nothing
end

function advance_tracers!(
    tracers::TracerState{T},
    solver::AbstractFlowSolver{2,T},
    scenario::Scenario{2,T},
    geometry::NacaFoil{2,T},
    control::ControlState{T},
    timestep::T,
) where {T}
    initial_velocity = sample_velocity(solver, tracers.positions)
    midpoint = tracers.positions .+ T(0.5) .* timestep .* initial_velocity
    midpoint_velocity = sample_velocity(solver, midpoint)
    tracers.positions .+= timestep .* midpoint_velocity
    x0, x1 = scenario.domain.bounds[1]
    y0, y1 = scenario.domain.bounds[2]
    tracers.history_cursor = mod1(tracers.history_cursor + 1, size(tracers.history, 3))
    for index in axes(tracers.positions, 2)
        outside_nonperiodic =
            (:x ∉ scenario.domain.periodic_axes &&
                !(x0 <= tracers.positions[1, index] <= x1)) ||
            (:y ∉ scenario.domain.periodic_axes &&
                !(y0 <= tracers.positions[2, index] <= y1))
        if outside_nonperiodic
            _respawn_tracer!(
                tracers, index, scenario, geometry, control.angle_degrees,
                :inlet, :boundary_exit,
            )
            continue
        end
        wrapped = false
        if :x in scenario.domain.periodic_axes &&
                !(x0 <= tracers.positions[1, index] <= x1)
            tracers.positions[1, index] =
                x0 + mod(tracers.positions[1, index] - x0, x1 - x0)
            wrapped = true
        end
        if :y in scenario.domain.periodic_axes &&
                !(y0 <= tracers.positions[2, index] <= y1)
            tracers.positions[2, index] =
                y0 + mod(tracers.positions[2, index] - y0, y1 - y0)
            wrapped = true
        end
        tracers.ages[index] += timestep
        point = SVector{2,T}(tracers.positions[:, index])
        expired = tracers.mode == :display && tracers.ages[index] >= tracers.lifetimes[index]
        distance = signed_distance(geometry, point, control.angle_degrees)
        inside_solid = distance <= zero(T)
        if inside_solid
            point_matrix = reshape(collect(point), 1, 2)
            normal = vec(normals(geometry, point_matrix, control.angle_degrees))
            normal_norm = sqrt(sum(abs2, normal))
            shallow_limit = T(0.5) * min(dx(scenario.domain), dy(scenario.domain))
            projectable = distance >= -shallow_limit && isfinite(distance) &&
                all(isfinite, normal) && normal_norm > T(1.0e-8)
            if projectable
                normal ./= normal_norm
                tracers.positions[:, index] .-= (distance - T(1.0e-4)) .* normal
                tracers.history[:, index, tracers.history_cursor] = tracers.positions[:, index]
                tracers.history_generations[index, tracers.history_cursor] =
                    tracers.generations[index]
            else
                placement = tracers.mode == :material ? :inlet : :domain
                _respawn_tracer!(
                    tracers,
                    index,
                    scenario,
                    geometry,
                    control.angle_degrees,
                    placement,
                    :invalid_collision,
                )
                continue
            end
        end
        if expired
            _respawn_tracer!(
                tracers, index, scenario, geometry, control.angle_degrees,
                :domain, :lifetime_expiry,
            )
            continue
        end
        if wrapped
            tracers.generations[index] += 1
            tracers.recycle_counters[:periodic_wrap] += 1
            _reset_tracer_history!(tracers, index)
        end
        tracers.history[:, index, tracers.history_cursor] = tracers.positions[:, index]
        tracers.history_generations[index, tracers.history_cursor] =
            tracers.generations[index]
    end
    return nothing
end

function path_segments(tracers::TracerState{T}) where {T}
    history_length = size(tracers.history, 3)
    tracer_count = size(tracers.positions, 2)
    chronological = [mod1(tracers.history_cursor + offset, history_length) for offset in 1:history_length]
    valid_count = count(
        tracers.history_generations[tracer_index, chronological[history_index]] ==
            tracers.history_generations[tracer_index, chronological[history_index + 1]]
        for tracer_index in 1:tracer_count, history_index in 1:(history_length - 1)
    )
    output = Matrix{T}(undef, 2, 2 * valid_count)
    output_index = 1
    for tracer_index in 1:tracer_count, history_index in 1:(history_length - 1)
        tracers.history_generations[tracer_index, chronological[history_index]] ==
            tracers.history_generations[tracer_index, chronological[history_index + 1]] || continue
        output[:, output_index] = tracers.history[:, tracer_index, chronological[history_index]]
        output[:, output_index + 1] = tracers.history[:, tracer_index, chronological[history_index + 1]]
        output_index += 2
    end
    return output
end

"""Deterministically reseed all visible tracers and invalidate their paths."""
function reseed_tracers!(
    tracers::TracerState{T},
    scenario::Scenario{2,T},
    geometry::NacaFoil{2,T},
    angle_degrees::T,
    reason::Symbol = :forced_recovery,
) where {T}
    for tracer in axes(tracers.positions, 2)
        _respawn_tracer!(
            tracers,
            tracer,
            scenario,
            geometry,
            angle_degrees,
            :domain,
            reason,
        )
        tracers.ages[tracer] =
            _random_fraction(tracers.rng, T) * tracers.lifetimes[tracer]
    end
    tracers.history_cursor = size(tracers.history, 3)
    return size(tracers.positions, 2)
end

mutable struct PresentationState{T<:AbstractFloat}
    vorticity_visible::Bool
    crop_enabled::Bool
    tracer_mode::Symbol
    diagnostic_mode::Symbol
    diagnostic_interval::T
    diagnostic_elapsed::T
    diagnostics::Diagnostics
    vorticity::Matrix{T}
    diagnostic_revision::UInt64
    vorticity_solver_state_revision::Union{Nothing,Int}
end

const _POSE_SAMPLE_WINDOW_SECONDS = 0.08
const _POSE_ONLY_RELEASE_SPEED_RATIO = 0.5
const _POSE_ONLY_RELEASE_STEPS = 2
const _MAX_RESOLVED_TIP_SPEED_RATIO = 8

mutable struct ViewerModel{T<:AbstractFloat}
    scenario::Scenario{2,T}
    geometry::NacaFoil{2,T}
    solver::AbstractFlowSolver{2,T}
    tracers::TracerState{T}
    presentation::PresentationState{T}
    paused::Bool
    manual_angle::Union{Nothing,T}
    angular_velocity::T
    playback_rate::T
    simulation_time::T
    step_rate::Float64
    simulated_seconds_per_wall_second::Float64
    last_substeps::Int
    last_max_speed::T
    status_message::String
    tuning_values::Dict{String,InteractiveTuningValue}
    drag_active::Bool
    pose_only_drag::Bool
    pose_only_release_pending::Bool
    pose_only_calm_steps::Int
    pose_only_guarded_trial::Bool
    last_requested_angular_velocity::T
    recovery_count::Int
    recovery_reason::Union{Nothing,Symbol}
    recovery_stage::Union{Nothing,Symbol}
    pose_samples::Vector{Tuple{Float64,T}}
    last_pose_received_at::Union{Nothing,Float64}
    metrics_warming::Bool
    warm_validation_pending::Bool
    presentation_failure::Union{Nothing,String}
    solver_epoch::Int
end

function _create_solver(::Type{T}, solver_id::AbstractString) where {T<:AbstractFloat}
    solver_id == "stable-fluids" && return StableFluidsSolver(T)
    solver_id == "lbm-d2q9" && return LBMSolver(T)
    solver_id == "pic-flip" && return PicFlipSolver(T)
    throw(ArgumentError("Julia solver $solver_id is not available yet"))
end

function ViewerModel(
    scenario::Scenario{2,T};
    solver_id::AbstractString = "stable-fluids",
    tracer_count::Union{Nothing,Int} = nothing,
    history_length::Int = 12,
) where {T}
    geometry = NacaFoil(scenario.foil)
    solver = _create_solver(T, solver_id)
    initialize!(solver, scenario, geometry, scenario.seed)
    initial_control = control_at(scenario, zero(T))
    crop_available = option(scenario, "viewer_crop_cells", 0) > 0
    crop_enabled = option(scenario, "viewer_crop_default", false)
    crop_enabled isa Bool || throw(ArgumentError("viewer_crop_default must be a boolean"))
    area = (scenario.domain.bounds[1][2] - scenario.domain.bounds[1][1]) *
        (scenario.domain.bounds[2][2] - scenario.domain.bounds[2][1])
    selected_count = something(tracer_count, clamp(round(Int, T(256) * area), 2048, 8192))
    tracers = TracerState(
        scenario,
        geometry,
        initial_control.angle_degrees;
        count = selected_count,
        history_length,
    )
    presentation = PresentationState(
        true,
        crop_available && crop_enabled,
        tracers.mode,
        :cadenced,
        T(0.1),
        zero(T),
        Diagnostics(Dict{String,Float64}(), String[]),
        zeros(T, nx(scenario.domain), ny(scenario.domain)),
        UInt64(0),
        nothing,
    )
    model = ViewerModel(
        scenario,
        geometry,
        solver,
        tracers,
        presentation,
        false,
        nothing,
        zero(T),
        one(T),
        zero(T),
        0.0,
        0.0,
        0,
        zero(T),
        "ready",
        Dict{String,InteractiveTuningValue}(),
        false,
        false,
        false,
        0,
        false,
        zero(T),
        0,
        nothing,
        nothing,
        Tuple{Float64,T}[],
        nothing,
        true,
        false,
        nothing,
        0,
    )
    _remember_active_tuning!(model)
    _refresh_presentation!(model; force_vorticity = true)
    return model
end

function _remember_active_tuning!(model::ViewerModel)
    selected = interactive_tuning(model.solver)
    selected === nothing || (model.tuning_values[solver_info(model.solver).id] = selected.value)
    return selected
end

function _apply_saved_tuning!(model::ViewerModel, solver::AbstractFlowSolver = model.solver)
    solver_id = solver_info(solver).id
    selected = interactive_tuning(solver)
    if selected !== nothing && haskey(model.tuning_values, solver_id)
        selected = apply_interactive_tuning!(solver, model.tuning_values[solver_id])
    end
    selected === nothing || (model.tuning_values[solver_id] = selected.value)
    return selected
end

function viewer_session_state(model::ViewerModel)
    phase = model.paused ?
        (model.presentation_failure === nothing ? :paused : :failed) :
        (model.metrics_warming ? :warming : :running)
    return (
        phase = phase,
        motion_mode = model.pose_only_drag ? Symbol("pose-only") : :resolved,
        schedule_active = model.manual_angle === nothing,
        diagnostic_mode = model.presentation.diagnostic_mode,
        recovery_epoch = model.recovery_count,
    )
end

function requested_tip_speed_ratio(model::ViewerModel{T}) where {T}
    tip_speed = abs(T(deg2rad(model.last_requested_angular_velocity))) * model.scenario.foil.chord
    return tip_speed / reference_speed(model.scenario)
end

rapid_drag_attempted(model::ViewerModel) = model.drag_active && requested_tip_speed_ratio(model) > 1

function enable_pose_only_drag!(model::ViewerModel)
    model.pose_only_drag = true
    model.pose_only_release_pending = false
    model.pose_only_calm_steps = 0
    model.pose_only_guarded_trial = false
    return nothing
end

function _disable_pose_only_drag!(model::ViewerModel; guard_next_failure::Bool = false)
    model.pose_only_drag = false
    model.pose_only_release_pending = false
    model.pose_only_calm_steps = 0
    model.pose_only_guarded_trial = guard_next_failure
    return nothing
end

function _pause_for_presentation_failure!(model::ViewerModel, stage::AbstractString, error)
    model.presentation_failure = "$stage $(typeof(error)): $(sprint(showerror, error))"
    model.status_message = "presentation error: $(something(model.presentation_failure))"
    model.metrics_warming = true
    model.paused = true
    return nothing
end

function _settle_idle_drag!(model::ViewerModel{T}) where {T}
    received_at = model.last_pose_received_at
    if !model.drag_active || received_at === nothing ||
            time_ns() / 1.0e9 - received_at <= _POSE_SAMPLE_WINDOW_SECONDS
        return nothing
    end
    model.angular_velocity = zero(T)
    model.last_requested_angular_velocity = zero(T)
    empty!(model.pose_samples)
    model.last_pose_received_at = nothing
    return nothing
end

function update!(model::ViewerModel{T}) where {T}
    model.paused && return snapshot(model)
    _settle_idle_drag!(model)
    was_warming = model.metrics_warming
    target_dt = model.scenario.output_dt * model.playback_rate
    current_time = model.simulation_time
    next_time = current_time + target_dt
    requested_control = model.manual_angle === nothing ? control_at(model.scenario, next_time) :
        ControlState(next_time, something(model.manual_angle), model.angular_velocity)
    model.last_requested_angular_velocity = requested_control.angular_velocity_degrees
    control = model.pose_only_drag ?
        ControlState(next_time, requested_control.angle_degrees, zero(T)) : requested_control
    started = time_ns()
    report = advance!(model.solver, control, target_dt)
    model.warm_validation_pending = false
    model.simulation_time = next_time
    elapsed = (time_ns() - started) / 1.0e9
    model.step_rate = elapsed > 0 ? inv(elapsed) : Inf
    model.simulated_seconds_per_wall_second = elapsed > 0 ? target_dt / elapsed : Inf
    model.last_substeps = report.substeps
    model.last_max_speed = report.max_speed
    try
        advance_tracers!(
            model.tracers,
            model.solver,
            model.scenario,
            model.geometry,
            control,
            target_dt,
        )
    catch error
        model.metrics_warming = true
        _pause_for_presentation_failure!(model, "tracer", error)
        return snapshot(model)
    end
    model.presentation.diagnostic_elapsed += target_dt
    if model.presentation.diagnostic_mode == Symbol("every-step") || was_warming ||
            model.presentation.diagnostic_elapsed >= model.presentation.diagnostic_interval
        try
            _refresh_presentation!(model)
        catch error
            model.metrics_warming = true
            _pause_for_presentation_failure!(model, "diagnostic", error)
            return snapshot(model)
        end
    end
    model.metrics_warming = false
    model.presentation_failure = nothing
    if model.pose_only_drag
        if model.pose_only_release_pending && !model.drag_active
            _disable_pose_only_drag!(model; guard_next_failure = true)
        elseif requested_tip_speed_ratio(model) <= T(_POSE_ONLY_RELEASE_SPEED_RATIO)
            model.pose_only_calm_steps += 1
            model.pose_only_calm_steps >= _POSE_ONLY_RELEASE_STEPS &&
                _disable_pose_only_drag!(model; guard_next_failure = true)
        else
            model.pose_only_calm_steps = 0
        end
    end
    return snapshot(model)
end

function _viewer_vorticity(
    velocity::Array{T,3},
    scenario::Scenario{2,T},
    geometry::NacaFoil{2,T},
    angle_degrees::T,
) where {T<:AbstractFloat}
    omega = vorticity(velocity, scenario.domain)
    solid = solid_mask(geometry, scenario.domain, angle_degrees)
    omega[solid] .= zero(T)
    fluid_magnitude = abs.(omega[.!solid])
    isempty(fluid_magnitude) && return omega
    maximum_magnitude = maximum(fluid_magnitude)
    percentile_index = clamp(
        ceil(Int, T(0.995) * T(length(fluid_magnitude))),
        1,
        length(fluid_magnitude),
    )
    percentile = partialsort!(fluid_magnitude, percentile_index)
    scale = max(percentile, T(0.2) * maximum_magnitude, T(1.0e-6))
    return tanh.(omega ./ scale)
end

function _refresh_presentation!(
    model::ViewerModel{T};
    force_vorticity::Bool = false,
) where {T<:AbstractFloat}
    solver_diagnostics = diagnostics(model.solver)
    solver_diagnostics.state_revision == state_revision(model.solver) ||
        error("solver diagnostics describe a stale state revision")
    model.presentation.diagnostics = solver_diagnostics
    if model.presentation.vorticity_visible || force_vorticity
        velocity = cell_velocity(model.solver)
        angle = something(
            model.manual_angle,
            control_at(model.scenario, model.simulation_time).angle_degrees,
        )
        model.presentation.vorticity = _viewer_vorticity(
            velocity,
            model.scenario,
            model.geometry,
            angle,
        )
        model.presentation.vorticity_solver_state_revision = state_revision(model.solver)
    end
    model.presentation.diagnostic_elapsed = zero(T)
    model.presentation.diagnostic_revision += 1
    return nothing
end

function snapshot(model::ViewerModel{T}) where {T}
    solver_diagnostics = model.presentation.diagnostics
    angle = model.manual_angle === nothing ?
        control_at(model.scenario, model.simulation_time).angle_degrees :
        something(model.manual_angle)
    energy = get(solver_diagnostics.values, "kinetic_energy", 0.0)
    enstrophy_value = get(solver_diagnostics.values, "enstrophy", 0.0)
    divergence = get(
        solver_diagnostics.values,
        "divergence_linf",
        get(solver_diagnostics.values, "divergence_l2", 0.0),
    )
    leakage = get(solver_diagnostics.values, "solid_leakage", 0.0)
    tuning = interactive_tuning(model.solver)
    tuning_display = tuning === nothing ? "" :
        "  $(tuning.label)=$(tuning.display_value)"
    effective_reynolds = haskey(solver_diagnostics.values, "effective_reynolds") ?
        string("  Re_eff=", round(Int, solver_diagnostics.values["effective_reynolds"])) : ""
    motion = model.pose_only_drag ? "  motion=pose-only" : ""
    paused = model.paused ? "  PAUSED" : ""
    recovery_epoch = model.recovery_count > 0 ? "  recovery_epoch=$(model.recovery_count)" : ""
    measurements = model.metrics_warming ?
        "step=     —/s  sim/wall=     —  sub=—  max|u|=     —" :
        @sprintf(
            "step=%6.1f/s  sim/wall=%6.2f  sub=%d  max|u|=%6.2f",
            model.step_rate,
            model.simulated_seconds_per_wall_second,
            model.last_substeps,
            model.last_max_speed,
        )
    diagnostic_measurements = model.metrics_warming ?
        "\nE=—  Ω=—  div=—  leak=—" :
        string(
            "\nE=", round(energy; digits = 3),
            "  Ω=", round(enstrophy_value; digits = 3),
            "  div=", round(divergence; sigdigits = 3),
            "  leak=", round(leakage; sigdigits = 3),
        )
    status = string(
        solver_info(model.solver).display_name,
        @sprintf(
            "  t=%7.2f  AoA=%6.1f°  Re=%7.0f  rate=%4.2fx  ",
            model.simulation_time,
            angle,
            reynolds(model.solver),
            model.playback_rate,
        ),
        measurements,
        diagnostic_measurements,
        "  tracers=", model.tracers.mode,
        "  vort=", model.presentation.vorticity_visible ? "on" : "off",
        "  view=", model.presentation.crop_enabled ? "cropped" : "full",
        "  diag=", model.presentation.diagnostic_mode,
        tuning_display,
        effective_reynolds,
        motion,
        recovery_epoch,
        paused,
        "  ", model.status_message,
    )
    published_vorticity = model.presentation.vorticity_visible ?
        copy(model.presentation.vorticity) : similar(model.presentation.vorticity, 0, 0)
    return ViewerSnapshot(
        UInt64(0),
        UInt64(0),
        model.solver_epoch,
        state_revision(model.solver),
        solver_diagnostics.state_revision,
        model.presentation.vorticity_solver_state_revision,
        model.simulation_time,
        angle,
        solver_info(model.solver).id,
        copy(model.tracers.positions),
        path_segments(model.tracers),
        published_vorticity,
        copy(solver_diagnostics.values),
        status,
        model.paused,
        model.presentation.vorticity_visible,
        model.presentation.crop_enabled,
        model.tracers.mode,
        viewer_session_state(model).phase,
        viewer_session_state(model).motion_mode,
        model.presentation.diagnostic_mode,
        model.manual_angle === nothing,
        model.recovery_count,
        copy(model.tracers.recycle_counters),
    )
end

toggle_pause!(model::ViewerModel) = (model.paused = !model.paused)
function toggle_vorticity!(model::ViewerModel)
    model.presentation.vorticity_visible = !model.presentation.vorticity_visible
    if model.presentation.vorticity_visible
        try
            _refresh_presentation!(model; force_vorticity = true)
            model.presentation_failure = nothing
        catch error
            _pause_for_presentation_failure!(model, "diagnostic", error)
        end
    end
    return model.presentation.vorticity_visible
end

function toggle_diagnostics!(model::ViewerModel)
    model.presentation.diagnostic_mode = model.presentation.diagnostic_mode == :cadenced ?
        Symbol("every-step") : :cadenced
    try
        _refresh_presentation!(model)
        model.presentation_failure = nothing
    catch error
        _pause_for_presentation_failure!(model, "diagnostic", error)
    end
    return model.presentation.diagnostic_mode
end

function toggle_crop!(model::ViewerModel)
    option(model.scenario, "viewer_crop_cells", 0) > 0 || return false
    model.presentation.crop_enabled = !model.presentation.crop_enabled
    return model.presentation.crop_enabled
end

function toggle_tracer_mode!(model::ViewerModel)
    model.tracers.mode = model.tracers.mode == :display ? :material : :display
    if model.tracers.mode == :display
        for index in eachindex(model.tracers.ages)
            model.tracers.ages[index] =
                _random_fraction(model.tracers.rng, eltype(model.tracers.ages)) *
                model.tracers.lifetimes[index]
        end
    end
    model.presentation.tracer_mode = model.tracers.mode
    return model.tracers.mode
end

function set_angle!(
    model::ViewerModel{T},
    angle_degrees::Real,
    timestamp::Real = time_ns() / 1.0e9,
) where {T}
    selected = clamp(T(angle_degrees), T(-30), T(30))
    selected_time = Float64(timestamp)
    isfinite(selected_time) || throw(ArgumentError("pose timestamp must be finite"))
    model.last_pose_received_at = time_ns() / 1.0e9
    if !isempty(model.pose_samples) &&
            (selected_time <= last(model.pose_samples)[1] ||
             selected_time - last(model.pose_samples)[1] > _POSE_SAMPLE_WINDOW_SECONDS)
        empty!(model.pose_samples)
        model.angular_velocity = zero(T)
    end
    push!(model.pose_samples, (selected_time, selected))
    cutoff = selected_time - _POSE_SAMPLE_WINDOW_SECONDS
    while length(model.pose_samples) > 2 && model.pose_samples[2][1] < cutoff
        popfirst!(model.pose_samples)
    end
    measured = zero(T)
    if length(model.pose_samples) >= 2
        first_time, first_angle = first(model.pose_samples)
        measured = (selected - first_angle) / T(selected_time - first_time)
    end
    maximum = T(rad2deg(
        _MAX_RESOLVED_TIP_SPEED_RATIO * reference_speed(model.scenario) /
        model.scenario.foil.chord,
    ))
    model.manual_angle = selected
    model.angular_velocity = clamp(measured, -maximum, maximum)
    model.last_requested_angular_velocity = model.angular_velocity
    model.drag_active = true
    return selected
end

function release_angle!(model::ViewerModel{T}) where {T}
    model.angular_velocity = zero(T)
    model.last_requested_angular_velocity = zero(T)
    empty!(model.pose_samples)
    model.last_pose_received_at = nothing
    model.drag_active = false
    model.pose_only_release_pending = model.pose_only_drag
    return nothing
end

function set_reynolds!(model::ViewerModel{T}, selected::Real) where {T}
    chosen = clamp(T(selected), T(50), T(100_000))
    previous_revision = state_revision(model.solver)
    set_reynolds!(model.solver, chosen)
    state_revision(model.solver) == previous_revision || _invalidate_solver_measurements!(model)
    exponent = log10(T(1.5))
    model.playback_rate = clamp((chosen / model.scenario.reynolds)^exponent, T(0.5), T(2))
    return chosen
end

function _invalidate_solver_measurements!(model::ViewerModel{T}) where {T}
    model.step_rate = 0.0
    model.simulated_seconds_per_wall_second = 0.0
    model.last_substeps = 0
    model.last_max_speed = zero(T)
    model.metrics_warming = true
    model.warm_validation_pending = false
    model.presentation.diagnostics = Diagnostics(
        Dict{String,Float64}(),
        String[],
        state_revision(model.solver),
    )
    model.presentation.diagnostic_elapsed = zero(T)
    model.presentation.vorticity_solver_state_revision = nothing
    return nothing
end

adjust_reynolds!(model::ViewerModel, decades::Real) =
    set_reynolds!(model, reynolds(model.solver) * 10.0^decades)
reset_reynolds!(model::ViewerModel) = set_reynolds!(model, model.scenario.reynolds)

function reset_viewer!(model::ViewerModel{T}) where {T}
    tracer_mode = model.tracers.mode
    solver = _create_solver(T, solver_info(model.solver).id)
    initialize!(solver, model.scenario, model.geometry, model.scenario.seed)
    model.solver = solver
    model.solver_epoch += 1
    empty!(model.tuning_values)
    _remember_active_tuning!(model)
    reseed_tracers!(
        model.tracers,
        model.scenario,
        model.geometry,
        control_at(model.scenario, zero(T)).angle_degrees,
        :scenario_reset,
    )
    model.presentation.tracer_mode = tracer_mode
    model.paused = false
    model.manual_angle = nothing
    model.angular_velocity = zero(T)
    model.playback_rate = one(T)
    model.simulation_time = zero(T)
    model.step_rate = 0.0
    model.simulated_seconds_per_wall_second = 0.0
    model.last_substeps = 0
    model.last_max_speed = zero(T)
    model.status_message = "reset"
    model.drag_active = false
    _disable_pose_only_drag!(model)
    model.last_requested_angular_velocity = zero(T)
    empty!(model.pose_samples)
    model.last_pose_received_at = nothing
    model.metrics_warming = true
    model.warm_validation_pending = false
    model.recovery_reason = nothing
    model.recovery_stage = nothing
    model.presentation_failure = nothing
    try
        _refresh_presentation!(
            model;
            force_vorticity = model.presentation.vorticity_visible,
        )
    catch error
        _pause_for_presentation_failure!(model, "diagnostic", error)
    end
    return nothing
end

function _state_at_control(state::CanonicalFlowState{2,T}, control::ControlState) where {T}
    return CanonicalFlowState(
        state.schema_version,
        state.bounds,
        state.resolution,
        state.periodic_axes,
        T(control.time),
        T(control.angle_degrees),
        T(control.angular_velocity_degrees),
        state.source_language,
        state.source_solver,
        copy(state.velocity),
        state.density === nothing ? nothing : copy(state.density),
    )
end

function _fresh_solver_at_control(
    model::ViewerModel{T},
    solver_id::AbstractString,
    control::ControlState;
    selected_reynolds::Real = reynolds(model.solver),
) where {T}
    incoming = _create_solver(T, solver_id)
    restart!(
        incoming,
        model.scenario,
        model.geometry,
        model.scenario.seed,
        RestartState(T(control.time), T(control.angle_degrees), T(selected_reynolds)),
    )
    _apply_saved_tuning!(model, incoming)
    state = export_state(incoming)
    isapprox(state.time, control.time; atol = 1.0e-12, rtol = 0) ||
        throw(NumericalFailure(:postcondition_failure, "fresh solver changed requested time"))
    isapprox(state.angle_degrees, control.angle_degrees; atol = 1.0e-12, rtol = 0) ||
        throw(NumericalFailure(:postcondition_failure, "fresh solver changed requested pose"))
    return incoming
end

function classify_viewer_failure(error)::Symbol
    message = lowercase(replace(sprint(showerror, error), "-" => ""))
    (occursin("nonfinite", message) || occursin("must be finite", message) ||
        occursin("nan", message)) &&
        return :nonfinite_state
    occursin("density", message) && return :invalid_density
    (occursin("projection", message) || occursin("pressure cg", message)) &&
        return :projection_failure
    occursin("geometry", message) && return :incompatible_geometry
    any(token -> occursin(token, message), ("resolution", "dimension", "domain", "bounds")) &&
        return :incompatible_domain
    any(token -> occursin(token, message), ("velocity", "wall", "cfl", "mach")) &&
        return :excessive_velocity
    return :unsupported_conversion
end

classify_viewer_failure(error::NumericalFailure)::Symbol = error.reason

function recover_solver!(
    model::ViewerModel{T},
    failure;
    reset_reynolds::Bool = false,
    post_import::Bool = false,
) where {T}
    current_time = model.simulation_time
    current_angle = something(
        model.manual_angle,
        control_at(model.scenario, current_time).angle_degrees,
    )
    selected_reynolds = reset_reynolds ? model.scenario.reynolds : reynolds(model.solver)
    recovery_control = ControlState(current_time, current_angle, zero(T))
    model.solver = _fresh_solver_at_control(
        model,
        solver_info(model.solver).id,
        recovery_control;
        selected_reynolds,
    )
    model.solver_epoch += 1
    moved = reseed_tracers!(
        model.tracers,
        model.scenario,
        model.geometry,
        current_angle,
    )
    model.angular_velocity = zero(T)
    model.manual_angle = current_angle
    empty!(model.pose_samples)
    model.last_pose_received_at = nothing
    model.recovery_count += 1
    model.recovery_reason = classify_viewer_failure(failure)
    model.recovery_stage = post_import ? Symbol("post-import") : Symbol("ordinary-step")
    model.step_rate = 0.0
    model.simulated_seconds_per_wall_second = 0.0
    model.last_substeps = 0
    model.last_max_speed = zero(T)
    model.metrics_warming = true
    model.warm_validation_pending = false
    reset_notice = reset_reynolds ? "; Re reset" : ""
    reason = model.recovery_reason
    stage = model.recovery_stage
    model.status_message =
        "fresh restart reason=$reason; stage=$stage; private-state-discarded; " *
        "reseeded=$moved$reset_notice"
    model.presentation_failure = nothing
    try
        _refresh_presentation!(
            model;
            force_vorticity = model.presentation.vorticity_visible,
        )
    catch error
        _pause_for_presentation_failure!(model, "diagnostic", error)
    end
    return nothing
end

const _TRANSIENT_IMPORT_FAILURES = Set((
    :excessive_velocity,
    :stability_limit,
    :nonfinite_state,
    :convergence_failure,
    :projection_failure,
    :invalid_density,
    :invalid_population,
    :transfer_failure,
    :postcondition_failure,
))

function _reject_or_fallback!(
    model::ViewerModel{T},
    solver_id::AbstractString,
    reason::Symbol,
    warnings::Vector{String} = String[],
) where {T}
    if reason ∉ _TRANSIENT_IMPORT_FAILURES
        model.status_message = "warm import rejected ($reason); source retained"
        return ImportOutcome(:rejected, reason; warnings)
    end
    source_id = solver_info(model.solver).id
    angle = something(
        model.manual_angle,
        control_at(model.scenario, model.simulation_time).angle_degrees,
    )
    control = ControlState(model.simulation_time, angle, zero(T))
    target_dt = model.scenario.output_dt * model.playback_rate
    validation_control = ControlState(model.simulation_time + target_dt, angle, zero(T))
    validation_started = time_ns()
    candidate = try
        selected = _fresh_solver_at_control(
            model,
            solver_id,
            control;
            selected_reynolds = reynolds(model.solver),
        )
        validation_report = advance!(selected, validation_control, target_dt)
        candidate_diagnostics = diagnostics(selected)
        candidate_diagnostics.state_revision == state_revision(selected) ||
            throw(NumericalFailure(
                :postcondition_failure,
                "fresh destination diagnostics describe a stale state revision",
            ))
        (selected, validation_report, candidate_diagnostics)
    catch error
        model.status_message =
            "warm import rejected ($reason); fresh destination failed " *
            "($(classify_viewer_failure(error))); source retained"
        return ImportOutcome(:rejected, reason; warnings = [warnings; sprint(showerror, error)])
    end
    incoming, validation_report, candidate_diagnostics = candidate
    validation_elapsed = max((time_ns() - validation_started) / 1.0e9, 1.0e-9)
    model.solver = incoming
    model.solver_epoch += 1
    model.simulation_time += validation_report.advanced_dt
    model.manual_angle = angle
    model.angular_velocity = zero(T)
    empty!(model.pose_samples)
    model.last_pose_received_at = nothing
    moved = reseed_tracers!(model.tracers, model.scenario, model.geometry, angle)
    model.recovery_count += 1
    model.recovery_reason = reason
    model.recovery_stage = Symbol("warm-import-fallback")
    model.step_rate = inv(validation_elapsed)
    model.simulated_seconds_per_wall_second = target_dt / validation_elapsed
    model.last_substeps = validation_report.substeps
    model.last_max_speed = validation_report.max_speed
    model.metrics_warming = false
    model.warm_validation_pending = true
    model.presentation.diagnostics = candidate_diagnostics
    model.presentation.diagnostic_elapsed = zero(T)
    model.presentation.diagnostic_revision += 1
    model.status_message =
        "fresh destination reason=$reason; stage=warm-import-fallback; " *
        "private-state-discarded; reseeded=$moved"
    model.presentation_failure = nothing
    _refresh_vorticity!(model; force = model.presentation.vorticity_visible)
    report = ImportReport(
        source_id,
        String(solver_id),
        ["canonical-flow-history", "solver-private-state"],
        ["fresh destination after rejected warm import"],
    )
    return ImportOutcome(:accepted, :none; report, warnings = copy(report.warnings))
end

function switch_solver!(model::ViewerModel{T}, solver_id::AbstractString) where {T}
    if solver_id == solver_info(model.solver).id
        model.status_message = "solver already active"
        report = ImportReport(solver_id, solver_id, String[], String[])
        return ImportOutcome(:accepted, :none; report)
    end
    _remember_active_tuning!(model)
    incoming = try
        _create_solver(T, solver_id)
    catch error
        model.status_message = "solver $solver_id is not available yet"
        return ImportOutcome(
            :rejected,
            :unsupported_conversion;
            warnings = [sprint(showerror, error)],
        )
    end
    selected_reynolds = reynolds(model.solver)
    state = export_state(model.solver)
    import_control = ControlState(
        T(state.time),
        something(model.manual_angle, T(state.angle_degrees)),
        model.pose_only_drag ? zero(T) : model.angular_velocity,
    )
    initialize!(incoming, model.scenario, model.geometry, model.scenario.seed)
    _apply_saved_tuning!(model, incoming)
    set_reynolds!(incoming, selected_reynolds)
    outcome = import_state!(incoming, _state_at_control(state, import_control), import_control)
    if !accepted(outcome)
        return _reject_or_fallback!(model, solver_id, outcome.reason, outcome.warnings)
    end
    report = something(outcome.report)
    try
        diagnostics(incoming)
    catch error
        error isa NumericalFailure || rethrow()
        detail = sprint(showerror, error)
        return _reject_or_fallback!(model, solver_id, error.reason, [detail])
    end
    target_dt = model.scenario.output_dt * model.playback_rate
    validation_time = model.simulation_time + target_dt
    scheduled = control_at(model.scenario, validation_time)
    requested_control = model.manual_angle === nothing ? scheduled :
        ControlState(validation_time, something(model.manual_angle), model.angular_velocity)
    validation_control = model.pose_only_drag ?
        ControlState(validation_time, requested_control.angle_degrees, zero(T)) :
        requested_control
    started = time_ns()
    validation_report = try
        selected_report = advance!(incoming, validation_control, target_dt)
        diagnostics(incoming)
        selected_report
    catch error
        error isa NumericalFailure || rethrow()
        reason = classify_viewer_failure(error)
        detail = sprint(showerror, error)
        return _reject_or_fallback!(model, solver_id, reason, [detail])
    end
    elapsed = max((time_ns() - started) / 1.0e9, 1.0e-9)
    candidate_diagnostics = diagnostics(incoming)
    candidate_diagnostics.state_revision == state_revision(incoming) ||
        error("solver diagnostics describe a stale state revision")

    model.solver = incoming
    model.solver_epoch += 1
    model.simulation_time = validation_time
    model.last_substeps = validation_report.substeps
    model.last_max_speed = validation_report.max_speed
    model.step_rate = inv(elapsed)
    model.simulated_seconds_per_wall_second = target_dt / elapsed
    model.metrics_warming = false
    model.warm_validation_pending = true
    model.presentation.diagnostics = candidate_diagnostics
    model.presentation.diagnostic_elapsed = zero(T)
    model.presentation.diagnostic_revision += 1
    model.status_message = isempty(report.warnings) ? "switched from $(state.source_solver)" :
        first(report.warnings)
    try
        advance_tracers!(
            model.tracers,
            incoming,
            model.scenario,
            model.geometry,
            validation_control,
            target_dt,
        )
        if model.presentation.vorticity_visible
            model.presentation.vorticity = _viewer_vorticity(
                cell_velocity(incoming),
                model.scenario,
                model.geometry,
                validation_control.angle_degrees,
            )
            model.presentation.vorticity_solver_state_revision = state_revision(incoming)
        end
        model.presentation_failure = nothing
    catch error
        _pause_for_presentation_failure!(model, "warm-switch presentation", error)
    end
    return ImportOutcome(:accepted, :none; report, warnings = copy(report.warnings))
end

function adjust_tuning!(model::ViewerModel, amount::Real)
    previous_revision = state_revision(model.solver)
    selected = adjust_interactive_tuning!(model.solver, amount < 0 ? -1 : 1)
    if selected !== nothing
        model.tuning_values[solver_info(model.solver).id] = selected.value
        model.status_message = "$(selected.label)=$(selected.display_value)"
        state_revision(model.solver) == previous_revision ||
            _invalidate_solver_measurements!(model)
        return true
    end
    model.status_message = "no adjustable tuning for $(solver_info(model.solver).display_name)"
    return false
end
