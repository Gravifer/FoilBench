mutable struct TracerState{T<:AbstractFloat}
    positions::Matrix{T}
    history::Array{T,3}
    history_cursor::Int
    ages::Vector{T}
    lifetimes::Vector{T}
    mode::Symbol
    rng::PCG32
end

struct ViewerSnapshot{T<:AbstractFloat}
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
    return TracerState(positions, history, history_length, ages, lifetimes, mode, rng)
end

function _reset_tracer_history!(tracers::TracerState, index::Int)
    for history_index in axes(tracers.history, 3)
        tracers.history[:, index, history_index] = tracers.positions[:, index]
    end
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
        :x in scenario.domain.periodic_axes &&
            (tracers.positions[1, index] = x0 + mod(tracers.positions[1, index] - x0, x1 - x0))
        :y in scenario.domain.periodic_axes &&
            (tracers.positions[2, index] = y0 + mod(tracers.positions[2, index] - y0, y1 - y0))
        tracers.ages[index] += timestep
        point = SVector{2,T}(tracers.positions[:, index])
        expired = tracers.mode == :display && tracers.ages[index] >= tracers.lifetimes[index]
        outside = _outside_domain(view(tracers.positions, :, index), scenario.domain)
        inside_solid = signed_distance(geometry, point, control.angle_degrees) <= zero(T)
        invalid = outside || inside_solid
        if expired || invalid
            placement = outside || tracers.mode == :material ? :inlet : :domain
            _seed_position!(
                tracers.positions,
                index,
                tracers.rng,
                scenario,
                geometry,
                control.angle_degrees,
                placement,
            )
            _reset_tracer_lifetime!(tracers, index)
            _reset_tracer_history!(tracers, index)
        else
            tracers.history[:, index, tracers.history_cursor] = tracers.positions[:, index]
        end
    end
    return nothing
end

function path_segments(tracers::TracerState{T}) where {T}
    history_length = size(tracers.history, 3)
    tracer_count = size(tracers.positions, 2)
    output = Matrix{T}(undef, 2, 2 * tracer_count * (history_length - 1))
    chronological = [mod1(tracers.history_cursor + offset, history_length) for offset in 1:history_length]
    output_index = 1
    for tracer_index in 1:tracer_count, history_index in 1:(history_length - 1)
        output[:, output_index] = tracers.history[:, tracer_index, chronological[history_index]]
        output[:, output_index + 1] = tracers.history[:, tracer_index, chronological[history_index + 1]]
        output_index += 2
    end
    return output
end

"""Redistribute only excess tracers so coarse spatial coverage is approximately uniform."""
function replenish_tracers!(
    tracers::TracerState{T},
    scenario::Scenario{2,T},
    geometry::NacaFoil{2,T},
    angle_degrees::T,
) where {T}
    tracer_count = size(tracers.positions, 2)
    width = scenario.domain.bounds[1][2] - scenario.domain.bounds[1][1]
    height = scenario.domain.bounds[2][2] - scenario.domain.bounds[2][1]
    columns = max(1, round(Int, sqrt(tracer_count * width / height)))
    rows = max(1, ceil(Int, tracer_count / columns))
    bin_count = columns * rows
    identifiers = Vector{Int}(undef, tracer_count)
    counts = zeros(Int, bin_count)
    x0, x1 = scenario.domain.bounds[1]
    y0, y1 = scenario.domain.bounds[2]
    for tracer in 1:tracer_count
        column = clamp(floor(Int, (tracers.positions[1, tracer] - x0) / width * columns) + 1, 1, columns)
        row = clamp(floor(Int, (tracers.positions[2, tracer] - y0) / height * rows) + 1, 1, rows)
        identifier = column + (row - 1) * columns
        identifiers[tracer] = identifier
        counts[identifier] += 1
    end
    desired = fill(tracer_count ÷ bin_count, bin_count)
    desired[1:(tracer_count % bin_count)] .+= 1
    ranks = zeros(Int, bin_count)
    donors = Int[]
    for tracer in 1:tracer_count
        identifier = identifiers[tracer]
        ranks[identifier] += 1
        ranks[identifier] > desired[identifier] && push!(donors, tracer)
    end
    destinations = Int[]
    for identifier in 1:bin_count, _ in 1:max(desired[identifier] - counts[identifier], 0)
        push!(destinations, identifier)
    end
    length(donors) == length(destinations) ||
        throw(ArgumentError("tracer coverage redistribution is unbalanced"))
    bin_width = width / T(columns)
    bin_height = height / T(rows)
    for (tracer, identifier) in zip(donors, destinations)
        column = mod1(identifier, columns)
        row = (identifier - 1) ÷ columns + 1
        placed = false
        for _ in 1:32
            x = x0 + (T(column - 1) + _random_fraction(tracers.rng, T)) * bin_width
            y = y0 + (T(row - 1) + _random_fraction(tracers.rng, T)) * bin_height
            point = SVector{2,T}(x, y)
            signed_distance(geometry, point, angle_degrees) > zero(T) || continue
            tracers.positions[:, tracer] .= point
            placed = true
            break
        end
        placed || _seed_position!(
            tracers.positions,
            tracer,
            tracers.rng,
            scenario,
            geometry,
            angle_degrees,
            :domain,
        )
        _reset_tracer_lifetime!(tracers, tracer)
        _reset_tracer_history!(tracers, tracer)
    end
    return length(donors)
end

mutable struct ViewerModel{T<:AbstractFloat}
    scenario::Scenario{2,T}
    geometry::NacaFoil{2,T}
    solver::AbstractFlowSolver{2,T}
    tracers::TracerState{T}
    paused::Bool
    vorticity_visible::Bool
    crop_enabled::Bool
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
    last_requested_angular_velocity::T
    recovery_count::Int
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
    return ViewerModel(
        scenario,
        geometry,
        solver,
        tracers,
        false,
        true,
        crop_available && crop_enabled,
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
        zero(T),
        0,
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
    return nothing
end

function _disable_pose_only_drag!(model::ViewerModel)
    model.pose_only_drag = false
    model.pose_only_release_pending = false
    model.pose_only_calm_steps = 0
    return nothing
end

function update!(model::ViewerModel{T}) where {T}
    model.paused && return snapshot(model)
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
    if model.pose_only_drag
        if model.pose_only_release_pending && !model.drag_active
            _disable_pose_only_drag!(model)
        elseif requested_tip_speed_ratio(model) <= T(0.5)
            model.pose_only_calm_steps += 1
            model.pose_only_calm_steps >= 2 && _disable_pose_only_drag!(model)
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

function snapshot(model::ViewerModel{T}) where {T}
    solver_diagnostics = diagnostics(model.solver)
    velocity = cell_velocity(model.solver)
    angle = model.manual_angle === nothing ?
        control_at(model.scenario, T(solver_diagnostics.values["time"])).angle_degrees :
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
    status = string(
        solver_info(model.solver).display_name,
        "  t=", round(model.simulation_time; digits = 2),
        "  AoA=", round(angle; digits = 1), "°",
        "  Re=", round(Int, reynolds(model.solver)),
        "  rate=", round(model.playback_rate; digits = 2), "x",
        "  step=", round(model.step_rate; digits = 1), "/s",
        "  sim/wall=", round(model.simulated_seconds_per_wall_second; digits = 2),
        "  sub=", model.last_substeps,
        "  max|u|=", round(model.last_max_speed; digits = 2),
        "\nE=", round(energy; digits = 3),
        "  Ω=", round(enstrophy_value; digits = 3),
        "  div=", round(divergence; sigdigits = 3),
        "  leak=", round(leakage; sigdigits = 3),
        "  tracers=", model.tracers.mode,
        "  vort=", model.vorticity_visible ? "on" : "off",
        "  view=", model.crop_enabled ? "cropped" : "full",
        transport,
        blend,
        effective_reynolds,
        motion,
        paused,
        "  ", model.status_message,
    )
    return ViewerSnapshot(
        T(solver_diagnostics.values["time"]),
        angle,
        solver_info(model.solver).id,
        copy(model.tracers.positions),
        path_segments(model.tracers),
        copy(velocity),
        _viewer_vorticity(velocity, model.scenario, model.geometry, angle),
        copy(solver_diagnostics.values),
        status,
        model.paused,
        model.vorticity_visible,
        model.crop_enabled,
        model.tracers.mode,
    )
end

toggle_pause!(model::ViewerModel) = (model.paused = !model.paused)
toggle_vorticity!(model::ViewerModel) = (model.vorticity_visible = !model.vorticity_visible)
toggle_crop!(model::ViewerModel) = (model.crop_enabled = !model.crop_enabled)

function toggle_tracer_mode!(model::ViewerModel)
    model.tracers.mode = model.tracers.mode == :display ? :material : :display
    if model.tracers.mode == :display
        for index in eachindex(model.tracers.ages)
            model.tracers.ages[index] =
                _random_fraction(model.tracers.rng, eltype(model.tracers.ages)) *
                model.tracers.lifetimes[index]
        end
    end
    return model.tracers.mode
end

function set_angle!(model::ViewerModel{T}, angle_degrees::Real, elapsed::Real = 1 / 60) where {T}
    selected = clamp(T(angle_degrees), T(-30), T(30))
    current_time = model.simulation_time
    scripted_angle = control_at(model.scenario, current_time).angle_degrees
    previous = something(model.manual_angle, scripted_angle)
    model.manual_angle = selected
    model.angular_velocity = (selected - previous) / max(T(elapsed), T(1.0e-4))
    model.last_requested_angular_velocity = model.angular_velocity
    model.drag_active = true
    return selected
end

function release_angle!(model::ViewerModel{T}) where {T}
    model.angular_velocity = zero(T)
    model.last_requested_angular_velocity = zero(T)
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
    model.paused = false
    model.manual_angle = nothing
    model.angular_velocity = zero(T)
    model.playback_rate = one(T)
    model.simulation_time = zero(T)
    model.step_rate = 0.0
    model.status_message = "reset"
    model.drag_active = false
    _disable_pose_only_drag!(model)
    model.last_requested_angular_velocity = zero(T)
    model.recovery_count = 0
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
    import_state!(incoming, fresh_state, control)
    return incoming
end

function recover_solver!(
    model::ViewerModel{T},
    failure;
    reset_reynolds::Bool = false,
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
    moved = replenish_tracers!(
        model.tracers,
        model.scenario,
        model.geometry,
        current_angle,
    )
    model.angular_velocity = zero(T)
    model.recovery_count += 1
    reset_notice = reset_reynolds ? "; Re reset" : ""
    model.status_message = "fresh restart after $(typeof(failure)); replenished=$moved$reset_notice"
    return nothing
end

function switch_solver!(model::ViewerModel{T}, solver_id::AbstractString) where {T}
    if solver_id == solver_info(model.solver).id
        model.status_message = "solver already active"
        return true
    end
    incoming = try
        _create_solver(T, solver_id)
    catch error
        model.status_message = "solver $solver_id is not available yet"
        return false
    end
    selected_reynolds = reynolds(model.solver)
    state = export_state(model.solver)
    control = ControlState(
        T(state.time),
        something(model.manual_angle, T(state.angle_degrees)),
        model.angular_velocity,
    )
    try
        initialize!(incoming, model.scenario, model.geometry, model.scenario.seed)
        _apply_stable_transport!(model, incoming)
        set_reynolds!(incoming, selected_reynolds)
        report = import_state!(incoming, state, control)
        model.solver = incoming
        model.status_message = isempty(report.warnings) ? "switched from $(state.source_solver)" :
            first(report.warnings)
        return true
    catch error
        try
            incoming = _fresh_solver_at_control(
                model,
                solver_id,
                ControlState(T(state.time), control.angle_degrees, zero(T));
                selected_reynolds,
            )
            model.solver = incoming
            moved = replenish_tracers!(
                model.tracers,
                model.scenario,
                model.geometry,
                T(control.angle_degrees),
            )
            model.status_message = "fresh restart after failed warm import; replenished=$moved"
            return true
        catch recovery_error
            model.status_message = "switch failed: $(typeof(error)); restart failed: $(typeof(recovery_error))"
            return false
        end
    end
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
