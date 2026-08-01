mutable struct TracerState{T<:AbstractFloat}
    positions::Matrix{T}
    history::Array{T,3}
    history_cursor::Int
    ages::Vector{Int}
    lifetimes::Vector{Int}
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
    mode::Symbol,
) where {T}
    x0, x1 = scenario.domain.bounds[1]
    y0, y1 = scenario.domain.bounds[2]
    for _ in 1:64
        x = mode == :material ? x0 + T(0.25) * dx(scenario.domain) :
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
        _seed_position!(positions, index, rng, scenario, geometry, angle_degrees, mode)
    end
    history = Array{T,3}(undef, 2, count, history_length)
    for history_index in 1:history_length
        history[:, :, history_index] = positions
    end
    ages = zeros(Int, count)
    base_lifetime = max(2, round(Int, T(4) / scenario.output_dt))
    lifetimes = [
        base_lifetime + round(Int, _random_fraction(rng, T) * T(base_lifetime))
        for _ in 1:count
    ]
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
        tracers.ages[index] += 1
        point = SVector{2,T}(tracers.positions[:, index])
        expired = tracers.mode == :display && tracers.ages[index] >= tracers.lifetimes[index]
        invalid = _outside_domain(view(tracers.positions, :, index), scenario.domain) ||
            signed_distance(geometry, point, control.angle_degrees) <= zero(T)
        if expired || invalid
            _seed_position!(
                tracers.positions,
                index,
                tracers.rng,
                scenario,
                geometry,
                control.angle_degrees,
                tracers.mode,
            )
            tracers.ages[index] = 0
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
    step_rate::Float64
    status_message::String
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
        false,
        option(scenario, "viewer_crop_cells", 0) > 0,
        nothing,
        zero(T),
        one(T),
        0.0,
        "ready",
    )
end

function update!(model::ViewerModel{T}) where {T}
    model.paused && return snapshot(model)
    target_dt = model.scenario.output_dt * model.playback_rate
    current_time = T(diagnostics(model.solver).values["time"])
    next_time = current_time + target_dt
    control = model.manual_angle === nothing ? control_at(model.scenario, next_time) :
        ControlState(next_time, something(model.manual_angle), model.angular_velocity)
    started = time_ns()
    advance!(model.solver, control, target_dt)
    elapsed = (time_ns() - started) / 1.0e9
    model.step_rate = elapsed > 0 ? inv(elapsed) : Inf
    advance_tracers!(
        model.tracers,
        model.solver,
        model.scenario,
        model.geometry,
        control,
        target_dt,
    )
    return snapshot(model)
end

function snapshot(model::ViewerModel{T}) where {T}
    solver_diagnostics = diagnostics(model.solver)
    velocity = cell_velocity(model.solver)
    angle = model.manual_angle === nothing ?
        control_at(model.scenario, T(solver_diagnostics.values["time"])).angle_degrees :
        something(model.manual_angle)
    status = string(
        solver_info(model.solver).display_name,
        "  step=", round(model.step_rate; digits = 1), "/s",
        "  Re=", round(Int, reynolds(model.solver)),
        "  ", model.status_message,
    )
    return ViewerSnapshot(
        T(solver_diagnostics.values["time"]),
        angle,
        solver_info(model.solver).id,
        copy(model.tracers.positions),
        path_segments(model.tracers),
        copy(velocity),
        vorticity(velocity, model.scenario.domain),
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
    return model.tracers.mode
end

function set_angle!(model::ViewerModel{T}, angle_degrees::Real, elapsed::Real = 1 / 60) where {T}
    selected = clamp(T(angle_degrees), T(-90), T(90))
    current_time = T(diagnostics(model.solver).values["time"])
    scripted_angle = control_at(model.scenario, current_time).angle_degrees
    previous = something(model.manual_angle, scripted_angle)
    model.manual_angle = selected
    model.angular_velocity = (selected - previous) / max(T(elapsed), T(1.0e-4))
    return selected
end

release_angle!(model::ViewerModel{T}) where {T} = (model.angular_velocity = zero(T))

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
    model.step_rate = 0.0
    model.status_message = "reset"
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
        set_reynolds!(incoming, selected_reynolds)
        report = import_state!(incoming, state, control)
        model.solver = incoming
        model.status_message = isempty(report.warnings) ? "switched from $(state.source_solver)" :
            first(report.warnings)
        return true
    catch error
        model.status_message = "switch failed: $(typeof(error))"
        return false
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
