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
end

struct ViewerSnapshot{T<:AbstractFloat}
    revision::UInt64
    applied_command::UInt64
    time::T
    angle_degrees::T
    solver_id::String
    tracer_positions::Matrix{T}
    path_segments::Matrix{T}
    velocity::Array{T,3}
    vorticity::Matrix{T}
    diagnostics::Dict{String,Float64}
    status::String
    paused::Bool
    vorticity_visible::Bool
    crop_enabled::Bool
    tracer_mode::Symbol
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
        wrapped && (tracers.generations[index] += 1)
        tracers.ages[index] += timestep
        point = SVector{2,T}(tracers.positions[:, index])
        expired = tracers.mode == :display && tracers.ages[index] >= tracers.lifetimes[index]
        outside = _outside_domain(view(tracers.positions, :, index), scenario.domain)
        distance = signed_distance(geometry, point, control.angle_degrees)
        inside_solid = distance <= zero(T)
        if outside || expired
            placement = outside || tracers.mode == :material ? :inlet : :domain
            _respawn_tracer!(
                tracers,
                index,
                scenario,
                geometry,
                control.angle_degrees,
                placement,
            )
        elseif inside_solid
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
                )
            end
        else
            tracers.history[:, index, tracers.history_cursor] = tracers.positions[:, index]
            tracers.history_generations[index, tracers.history_cursor] =
                tracers.generations[index]
        end
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
) where {T}
    for tracer in axes(tracers.positions, 2)
        _respawn_tracer!(
            tracers,
            tracer,
            scenario,
            geometry,
            angle_degrees,
            :domain,
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
    diagnostic_interval::T
    diagnostic_elapsed::T
    diagnostics::Diagnostics
    velocity::Array{T,3}
    vorticity::Matrix{T}
    diagnostic_revision::UInt64
end

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
    stable_transport::String
    drag_active::Bool
    pose_only_drag::Bool
    pose_only_release_pending::Bool
    pose_only_calm_steps::Int
    pose_only_guarded_trial::Bool
    last_requested_angular_velocity::T
    recovery_count::Int
    pose_samples::Vector{Tuple{Float64,T}}
    metrics_warming::Bool
    warm_validation_pending::Bool
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
    stable_transport = String(option(scenario, "stable_advection", "maccormack"))
    stable_transport in ("maccormack", "semi-lagrangian", "skew-rk2") ||
        throw(ArgumentError("unsupported Stable Fluids advection: $stable_transport"))
    crop_available = option(scenario, "viewer_crop_cells", 0) > 0
    crop_enabled = option(scenario, "viewer_crop_default", crop_available)
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
        T(0.1),
        zero(T),
        Diagnostics(Dict{String,Float64}(), String[]),
        zeros(T, nx(scenario.domain), ny(scenario.domain), 2),
        zeros(T, nx(scenario.domain), ny(scenario.domain)),
        UInt64(0),
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
        stable_transport,
        false,
        false,
        false,
        0,
        false,
        zero(T),
        0,
        Tuple{Float64,T}[],
        true,
        false,
    )
    _refresh_presentation!(model; force_vorticity = true)
    return model
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

function update!(model::ViewerModel{T}) where {T}
    model.paused && return snapshot(model)
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
    advance_tracers!(
        model.tracers,
        model.solver,
        model.scenario,
        model.geometry,
        control,
        target_dt,
    )
    model.presentation.diagnostic_elapsed += target_dt
    if was_warming ||
            model.presentation.diagnostic_elapsed >= model.presentation.diagnostic_interval
        _refresh_presentation!(model)
    end
    model.metrics_warming = false
    if model.pose_only_drag
        if model.pose_only_release_pending && !model.drag_active
            _disable_pose_only_drag!(model; guard_next_failure = true)
        elseif requested_tip_speed_ratio(model) <= T(0.5)
            model.pose_only_calm_steps += 1
            model.pose_only_calm_steps >= 2 &&
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
    model.presentation.diagnostics = diagnostics(model.solver)
    model.presentation.velocity = copy(cell_velocity(model.solver))
    if model.presentation.vorticity_visible || force_vorticity
        angle = something(
            model.manual_angle,
            control_at(model.scenario, model.simulation_time).angle_degrees,
        )
        model.presentation.vorticity = _viewer_vorticity(
            model.presentation.velocity,
            model.scenario,
            model.geometry,
            angle,
        )
    end
    model.presentation.diagnostic_elapsed = zero(T)
    model.presentation.diagnostic_revision += 1
    return nothing
end

function snapshot(model::ViewerModel{T}) where {T}
    solver_diagnostics = model.presentation.diagnostics
    velocity = model.presentation.velocity
    angle = model.manual_angle === nothing ?
        control_at(model.scenario, model.simulation_time).angle_degrees :
        something(model.manual_angle)
    energy = get(solver_diagnostics.values, "kinetic_energy", 0.0)
    enstrophy_value = get(solver_diagnostics.values, "enstrophy", 0.0)
    divergence = get(solver_diagnostics.values, "divergence_l2", 0.0)
    leakage = get(solver_diagnostics.values, "solid_leakage", 0.0)
    blend = model.solver isa PicFlipSolver ?
        "  blend=$(round(pic_flip_blend(model.solver); digits = 2))" : ""
    transport = model.solver isa StableFluidsSolver ?
        "  adv=$(stable_transport_mode(model.solver))" : ""
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
        transport,
        blend,
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
        model.simulation_time,
        angle,
        solver_info(model.solver).id,
        copy(model.tracers.positions),
        path_segments(model.tracers),
        copy(velocity),
        published_vorticity,
        copy(solver_diagnostics.values),
        status,
        model.paused,
        model.presentation.vorticity_visible,
        model.presentation.crop_enabled,
        model.tracers.mode,
    )
end

toggle_pause!(model::ViewerModel) = (model.paused = !model.paused)
function toggle_vorticity!(model::ViewerModel)
    model.presentation.vorticity_visible = !model.presentation.vorticity_visible
    model.presentation.vorticity_visible &&
        _refresh_presentation!(model; force_vorticity = true)
    return model.presentation.vorticity_visible
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
    current_time = model.simulation_time
    scripted_angle = control_at(model.scenario, current_time).angle_degrees
    selected_time = Float64(timestamp)
    if !isempty(model.pose_samples) && selected_time <= last(model.pose_samples)[1]
        selected_time = last(model.pose_samples)[1] + 1.0e-6
    end
    push!(model.pose_samples, (selected_time, selected))
    cutoff = selected_time - 0.08
    while length(model.pose_samples) > 2 && model.pose_samples[2][1] < cutoff
        popfirst!(model.pose_samples)
    end
    measured = zero(T)
    if length(model.pose_samples) >= 2
        first_time, first_angle = first(model.pose_samples)
        measured = (selected - first_angle) / T(selected_time - first_time)
    end
    maximum = T(rad2deg(
        8 * reference_speed(model.scenario) / model.scenario.foil.chord,
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
    model.drag_active = false
    model.pose_only_release_pending = model.pose_only_drag
    return nothing
end

function set_reynolds!(model::ViewerModel{T}, selected::Real) where {T}
    chosen = clamp(T(selected), T(50), T(100_000))
    set_reynolds!(model.solver, chosen)
    exponent = log10(T(1.5))
    model.playback_rate = clamp((chosen / model.scenario.reynolds)^exponent, T(0.5), T(2))
    return chosen
end

adjust_reynolds!(model::ViewerModel, decades::Real) =
    set_reynolds!(model, reynolds(model.solver) * 10.0^decades)
reset_reynolds!(model::ViewerModel) = set_reynolds!(model, model.scenario.reynolds)

function reset_viewer!(model::ViewerModel{T}) where {T}
    tracer_count = size(model.tracers.positions, 2)
    history_length = size(model.tracers.history, 3)
    tracer_mode = model.tracers.mode
    solver = _create_solver(T, solver_info(model.solver).id)
    initialize!(solver, model.scenario, model.geometry, model.scenario.seed)
    model.stable_transport = String(option(model.scenario, "stable_advection", "maccormack"))
    solver isa StableFluidsSolver &&
        set_stable_transport_mode!(solver, model.stable_transport)
    model.solver = solver
    model.tracers = TracerState(
        model.scenario,
        model.geometry,
        control_at(model.scenario, zero(T)).angle_degrees;
        count = tracer_count,
        history_length,
        mode = tracer_mode,
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
    model.metrics_warming = true
    model.warm_validation_pending = false
    _refresh_presentation!(
        model;
        force_vorticity = model.presentation.vorticity_visible,
    )
    return nothing
end

function _apply_stable_transport!(model::ViewerModel, solver::AbstractFlowSolver)
    solver isa StableFluidsSolver || return nothing
    set_stable_transport_mode!(solver, model.stable_transport)
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
    initialize!(incoming, model.scenario, model.geometry, model.scenario.seed)
    _apply_stable_transport!(model, incoming)
    set_reynolds!(incoming, selected_reynolds)
    fresh_state = _state_at_control(export_state(incoming), control)
    outcome = import_state!(incoming, fresh_state, control)
    accepted(outcome) || error(
        "fresh $solver_id solver rejected its own canonical state: $(outcome.reason)",
    )
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
    moved = reseed_tracers!(
        model.tracers,
        model.scenario,
        model.geometry,
        current_angle,
    )
    model.angular_velocity = zero(T)
    model.manual_angle = current_angle
    empty!(model.pose_samples)
    model.recovery_count += 1
    model.step_rate = 0.0
    model.simulated_seconds_per_wall_second = 0.0
    model.last_substeps = 0
    model.last_max_speed = zero(T)
    model.metrics_warming = true
    model.warm_validation_pending = false
    reset_notice = reset_reynolds ? "; Re reset" : ""
    reason = classify_viewer_failure(failure)
    stage = post_import ? "post-import" : "ordinary-step"
    model.status_message =
        "fresh restart reason=$reason; stage=$stage; private-state-discarded; " *
        "reseeded=$moved$reset_notice"
    _refresh_presentation!(
        model;
        force_vorticity = model.presentation.vorticity_visible,
    )
    return nothing
end

function switch_solver!(model::ViewerModel{T}, solver_id::AbstractString) where {T}
    if solver_id == solver_info(model.solver).id
        model.status_message = "solver already active"
        report = ImportReport(solver_id, solver_id, String[], String[])
        return ImportOutcome(:accepted, :none; report)
    end
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
    _apply_stable_transport!(model, incoming)
    set_reynolds!(incoming, selected_reynolds)
    outcome = import_state!(incoming, state, import_control)
    if !accepted(outcome)
        model.status_message =
            "warm import rejected ($(outcome.reason)); source retained"
        return outcome
    end
    report = something(outcome.report)
    try
        diagnostics(incoming)
    catch error
        error isa NumericalFailure || rethrow()
        detail = sprint(showerror, error)
        model.status_message = "warm import rejected ($(error.reason)); source retained"
        return ImportOutcome(:rejected, error.reason; warnings = [detail])
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
        model.status_message = "warm validation rejected ($reason); source retained"
        return ImportOutcome(:rejected, reason; warnings = [detail])
    end
    elapsed = max((time_ns() - started) / 1.0e9, 1.0e-9)
    candidate_diagnostics = diagnostics(incoming)
    candidate_velocity = cell_velocity(incoming)
    candidate_vorticity = model.presentation.vorticity_visible ?
        _viewer_vorticity(
            candidate_velocity,
            model.scenario,
            model.geometry,
            validation_control.angle_degrees,
        ) : model.presentation.vorticity

    model.solver = incoming
    model.simulation_time = validation_time
    model.last_substeps = validation_report.substeps
    model.last_max_speed = validation_report.max_speed
    model.step_rate = inv(elapsed)
    model.simulated_seconds_per_wall_second = target_dt / elapsed
    model.metrics_warming = false
    model.warm_validation_pending = true
    model.presentation.diagnostics = candidate_diagnostics
    model.presentation.velocity = candidate_velocity
    model.presentation.vorticity = candidate_vorticity
    model.presentation.diagnostic_elapsed = zero(T)
    model.presentation.diagnostic_revision += 1
    advance_tracers!(
        model.tracers,
        incoming,
        model.scenario,
        model.geometry,
        validation_control,
        target_dt,
    )
    model.status_message = isempty(report.warnings) ? "switched from $(state.source_solver)" :
        first(report.warnings)
    return ImportOutcome(:accepted, :none; report, warnings = copy(report.warnings))
end

function adjust_blend!(model::ViewerModel, amount::Real)
    if model.solver isa PicFlipSolver
        selected = set_pic_flip_blend!(model.solver, pic_flip_blend(model.solver) + amount)
        model.status_message = "PIC/FLIP blend=$(round(selected; digits = 2))"
        return true
    end
    model.status_message = "PIC/FLIP blend unavailable ($(amount >= 0 ? "+" : "-") requested)"
    return false
end

function adjust_tuning!(model::ViewerModel, amount::Real)
    if model.solver isa StableFluidsSolver
        model.stable_transport = amount < 0 ? "maccormack" : "skew-rk2"
        set_stable_transport_mode!(model.solver, model.stable_transport)
        model.status_message = "Stable transport=$(model.stable_transport)"
        return true
    end
    model.solver isa PicFlipSolver && return adjust_blend!(model, amount)
    model.status_message = "no adjustable tuning for $(solver_info(model.solver).display_name)"
    return false
end
