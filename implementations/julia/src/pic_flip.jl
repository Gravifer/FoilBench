const PIC_FLIP_INFO = SolverInfo(
    "pic-flip",
    "Blended PIC/FLIP",
    (2,),
    true,
    :julia_cpu,
)

mutable struct PicFlipSolver{T<:AbstractFloat} <: AbstractFlowSolver{2,T}
    scenario::Union{Nothing,Scenario{2,T}}
    geometry::Union{Nothing,NacaFoil{2,T}}
    positions::Matrix{T}
    particle_velocity::Matrix{T}
    grid_velocity::Array{T,3}
    solid::BitMatrix
    control::ControlState{T}
    time::T
    reynolds_value::T
    blend::T
    settling_steps::Int
    rng::PCG32
    projection_warning::String
    reseeded_last_step::Int
    swept_collisions_last_step::Int
    advance_count::Int
    population_interval::Int
    cfl::T
    projection_iterations::Int
    solid_angle::T
    unsupported_face_fraction::T
    revision::Int
end

function PicFlipSolver(::Type{T} = Float32) where {T<:AbstractFloat}
    return PicFlipSolver{T}(
        nothing,
        nothing,
        Matrix{T}(undef, 2, 0),
        Matrix{T}(undef, 2, 0),
        Array{T,3}(undef, 0, 0, 2),
        falses(0, 0),
        ControlState(zero(T), zero(T), zero(T)),
        zero(T),
        one(T),
        T(0.95),
        0,
        PCG32(0, 71),
        "",
        0,
        0,
        0,
        8,
        T(0.75),
        0,
        T(NaN),
        zero(T),
        0,
    )
end

solver_info(::PicFlipSolver) = PIC_FLIP_INFO
reynolds(solver::PicFlipSolver) = solver.reynolds_value
state_revision(solver::PicFlipSolver) = solver.revision
pic_flip_blend(solver::PicFlipSolver) = solver.blend

function set_reynolds!(solver::PicFlipSolver{T}, selected::Real) where {T}
    isfinite(selected) && selected > 0 ||
        throw(ArgumentError("Reynolds number must be finite and positive"))
    narrowed = T(selected)
    isfinite(narrowed) && narrowed > zero(T) ||
        throw(ArgumentError("Reynolds number is not representable in solver precision"))
    previous = solver.reynolds_value
    solver.reynolds_value = narrowed
    previous == narrowed || (solver.revision += 1)
    return ReynoldsOutcome(narrowed, solver.reynolds_value, String[])
end

function set_pic_flip_blend!(solver::PicFlipSolver{T}, selected::Real) where {T}
    narrowed = T(selected)
    isfinite(narrowed) || throw(ArgumentError("PIC/FLIP blend must be finite"))
    next_blend = clamp(narrowed, zero(T), one(T))
    previous = solver.blend
    solver.blend = next_blend
    previous == next_blend || (solver.revision += 1)
    return solver.blend
end

function interactive_tuning(solver::PicFlipSolver)
    selected = Float64(pic_flip_blend(solver))
    return InteractiveTuning(
        "pic-flip-blend", "blend", selected,
        string(round(selected; digits = 2)), selected > 0, selected < 1,
    )
end

function adjust_interactive_tuning!(solver::PicFlipSolver, direction::Integer)
    set_pic_flip_blend!(solver, pic_flip_blend(solver) + (direction < 0 ? -0.05 : 0.05))
    return interactive_tuning(solver)
end

function apply_interactive_tuning!(solver::PicFlipSolver, value::InteractiveTuningValue)
    value isa Float64 || throw(ArgumentError("PIC/FLIP tuning value must be numeric"))
    set_pic_flip_blend!(solver, value)
    return interactive_tuning(solver)
end

function _pic_require(solver::PicFlipSolver)
    solver.scenario === nothing && throw(ArgumentError("PIC/FLIP is not initialized"))
    solver.geometry === nothing && throw(ArgumentError("PIC/FLIP is not initialized"))
    return solver.scenario, solver.geometry
end

function _pic_seed_particles!(solver::PicFlipSolver{T}) where {T}
    scenario, _ = _pic_require(solver)
    fluid_cells = count(.!solver.solid)
    positions = Matrix{T}(undef, 2, 4 * fluid_cells)
    particle = 1
    for j in 1:ny(scenario.domain), i in 1:nx(scenario.domain)
        solver.solid[i, j] && continue
        for _ in 1:4
            jitter_x = T(next_float32!(solver.rng))
            jitter_y = T(next_float32!(solver.rng))
            positions[1, particle] = scenario.domain.bounds[1][1] +
                (T(i - 1) + T(0.1) + T(0.8) * jitter_x) * dx(scenario.domain)
            positions[2, particle] = scenario.domain.bounds[2][1] +
                (T(j - 1) + T(0.1) + T(0.8) * jitter_y) * dy(scenario.domain)
            particle += 1
        end
    end
    solver.positions = positions
    u, v = cell_to_faces(solver.grid_velocity)
    solver.particle_velocity = faces_to_particle(u, v, positions, scenario.domain)
    return nothing
end

function initialize!(
    solver::PicFlipSolver{T},
    scenario::Scenario{2,T},
    geometry::NacaFoil{2,T},
    seed::Integer,
) where {T}
    seed >= 0 || throw(ArgumentError("seed must be non-negative"))
    require_supported(solver_info(solver), scenario)
    solver.scenario = scenario
    solver.geometry = geometry
    solver.control = control_at(scenario, zero(T))
    solver.time = zero(T)
    solver.reynolds_value = scenario.reynolds
    set_pic_flip_blend!(solver, option(scenario, "pic_flip_blend", T(0.95)))
    solver.population_interval = option(scenario, "pic_population_interval", 8)
    solver.population_interval >= 1 || throw(ArgumentError("pic_population_interval must be positive"))
    solver.cfl = option(scenario, "pic_cfl", T(0.75))
    zero(T) < solver.cfl <= one(T) || throw(ArgumentError("pic_cfl must be in (0, 1]"))
    solver.rng = PCG32(seed, 71)
    solver.grid_velocity = _initial_velocity(scenario)
    solver.solid = solid_mask(geometry, scenario.domain, solver.control.angle_degrees)
    solver.solid_angle = solver.control.angle_degrees
    solver.settling_steps = 0
    solver.projection_warning = ""
    solver.reseeded_last_step = 0
    solver.swept_collisions_last_step = 0
    solver.advance_count = 0
    solver.projection_iterations = 0
    solver.revision = 0
    solver.unsupported_face_fraction = zero(T)
    _pic_seed_particles!(solver)
    fallback_u, fallback_v = cell_to_faces(solver.grid_velocity)
    u, v, solver.unsupported_face_fraction = particle_to_faces(
        solver.positions,
        solver.particle_velocity,
        scenario.domain,
        fallback_u,
        fallback_v,
    )
    solver.grid_velocity = faces_to_cell(u, v)
    return nothing
end

function restart!(
    solver::PicFlipSolver{T},
    scenario::Scenario{2,T},
    geometry::NacaFoil{2,T},
    seed::Integer,
    start::RestartState,
) where {T}
    validate_restart_state(start)
    initialize!(solver, scenario, geometry, seed)
    set_reynolds!(solver, start.reynolds)
    solver.time = T(start.time)
    solver.control = ControlState(solver.time, T(start.angle_degrees), zero(T))
    solver.solid = solid_mask(geometry, scenario.domain, solver.control.angle_degrees)
    solver.solid_angle = solver.control.angle_degrees
    solver.grid_velocity[:, :, 1] .= scenario.freestream[1]
    solver.grid_velocity[:, :, 2] .= scenario.freestream[2]
    wall = wall_velocity_grid(geometry, scenario.domain, solver.control)
    for index in CartesianIndices(solver.solid)
        solver.solid[index] || continue
        solver.grid_velocity[index, 1] = wall[index, 1]
        solver.grid_velocity[index, 2] = wall[index, 2]
    end
    _pic_seed_particles!(solver)
    solver.revision = 0
    solver.unsupported_face_fraction = zero(T)
    return nothing
end

function initialize!(
    solver::PicFlipSolver{T},
    scenario::Scenario{D,T},
    geometry::NacaFoil{D,T},
    seed::Integer,
) where {D,T}
    require_supported(solver_info(solver), scenario)
    error("unreachable PIC/FLIP dimension $D for $(typeof(geometry)) and seed $seed")
end

cell_velocity(solver::PicFlipSolver) = solver.grid_velocity

function _pic_wall_velocity(
    scenario::Scenario{2,T},
    point::SVector{2,T},
    control::ControlState,
) where {T}
    omega = T(deg2rad(control.angular_velocity_degrees))
    relative = point - scenario.foil.pivot
    return SVector{2,T}(-omega * relative[2], omega * relative[1])
end

function _pic_normal(
    geometry::NacaFoil{2,T},
    point::SVector{2,T},
    angle::Real,
) where {T}
    epsilon = max(geometry.spec.chord * T(1.0e-4), T(1.0e-6))
    gradient = SVector{2,T}(
        signed_distance(geometry, point + SVector(epsilon, zero(T)), angle) -
            signed_distance(geometry, point - SVector(epsilon, zero(T)), angle),
        signed_distance(geometry, point + SVector(zero(T), epsilon), angle) -
            signed_distance(geometry, point - SVector(zero(T), epsilon), angle),
    )
    length_value = sqrt(sum(abs2, gradient))
    length_value > epsilon && return gradient / length_value
    angle_radians = T(deg2rad(angle))
    return SVector{2,T}(-sin(angle_radians), cos(angle_radians))
end

function _pic_resolve_particle!(
    solver::PicFlipSolver{T},
    particle::Int,
    point::SVector{2,T},
    control::ControlState,
    margin::T,
) where {T}
    scenario, geometry = _pic_require(solver)
    distance = signed_distance(geometry, point, control.angle_degrees)
    distance > margin && return false
    normal = _pic_normal(geometry, point, control.angle_degrees)
    resolved = point - (distance - margin) * normal
    solver.positions[:, particle] .= resolved
    wall = _pic_wall_velocity(scenario, resolved, control)
    relative = SVector{2,T}(
        solver.particle_velocity[1, particle] - wall[1],
        solver.particle_velocity[2, particle] - wall[2],
    )
    inward = dot(relative, normal)
    inward < zero(T) && (relative -= inward * normal)
    solver.particle_velocity[1, particle] = wall[1] + relative[1]
    solver.particle_velocity[2, particle] = wall[2] + relative[2]
    return true
end

function _pic_resolve_collisions!(solver::PicFlipSolver{T}, control::ControlState) where {T}
    scenario, geometry = _pic_require(solver)
    margin = T(1.0e-4) * scenario.foil.chord
    radius = hypot(
        T(0.75) * scenario.foil.chord,
        (maximum_camber(geometry) + T(0.51) * thickness(geometry)) * scenario.foil.chord,
    ) + margin
    radius_squared = radius^2
    pivot_x = scenario.foil.pivot[1]
    pivot_y = scenario.foil.pivot[2]
    for particle in axes(solver.positions, 2)
        relative_x = solver.positions[1, particle] - pivot_x
        relative_y = solver.positions[2, particle] - pivot_y
        relative_x^2 + relative_y^2 <= radius_squared || continue
        point = SVector{2,T}(solver.positions[1, particle], solver.positions[2, particle])
        _pic_resolve_particle!(solver, particle, point, control, margin)
    end
    return nothing
end

function _pic_resolve_swept!(
    solver::PicFlipSolver{T},
    start_positions::AbstractMatrix{T},
    start_control::ControlState,
    control::ControlState,
) where {T}
    scenario, geometry = _pic_require(solver)
    radius = hypot(
        T(0.75) * scenario.foil.chord,
        (maximum_camber(geometry) + T(0.51) * thickness(geometry)) * scenario.foil.chord,
    )
    margin = T(0.05) * min(dx(scenario.domain), dy(scenario.domain))
    collisions = 0
    for particle in axes(solver.positions, 2)
        start = SVector{2,T}(start_positions[1, particle], start_positions[2, particle])
        finish = SVector{2,T}(solver.positions[1, particle], solver.positions[2, particle])
        segment = finish - start
        denominator = max(sum(abs2, segment), T(1.0e-12))
        closest_fraction = clamp(dot(scenario.foil.pivot - start, segment) / denominator, zero(T), one(T))
        norm(start + closest_fraction * segment - scenario.foil.pivot) <= radius + margin || continue
        wall_travel = abs(T(deg2rad(control.angle_degrees - start_control.angle_degrees))) * radius
        sample_count = clamp(
            ceil(Int, (norm(segment) + wall_travel) / max(T(0.1) * min(dx(scenario.domain), dy(scenario.domain)), eps(T))),
            2,
            16,
        )
        for sample in 1:sample_count
            fraction = T(sample) / T(sample_count)
            angle = T(start_control.angle_degrees) + fraction *
                (T(control.angle_degrees) - T(start_control.angle_degrees))
            sample_control = ControlState(
                T(start_control.time) + fraction * (T(control.time) - T(start_control.time)),
                angle,
                T(control.angular_velocity_degrees),
            )
            sample_point = start + fraction * segment
            if _pic_resolve_particle!(solver, particle, sample_point, sample_control, margin)
                collisions += 1
                break
            end
        end
    end
    solver.swept_collisions_last_step += collisions
    return nothing
end

function _pic_project!(
    solver::PicFlipSolver{T},
    velocity::AbstractArray{T,3},
    control::ControlState,
    timestep::T,
) where {T}
    u, v = cell_to_faces(velocity)
    _pic_project_faces!(solver, u, v, control, timestep)
    return faces_to_cell(u, v)
end

function _pic_project_faces!(
    solver::PicFlipSolver{T},
    u::AbstractMatrix{T},
    v::AbstractMatrix{T},
    control::ControlState,
    timestep::T,
) where {T}
    scenario, geometry = _pic_require(solver)
    wall = wall_velocity_grid(geometry, scenario.domain, control)
    channel_walls = option(scenario, "initial_condition", "") == "poiseuille"
    iterations, relative_residual, converged = project_faces!(
        u,
        v,
        scenario.domain,
        solver.solid,
        wall,
        scenario.freestream,
        timestep;
        channel_walls,
        tolerance = option(scenario, "pressure_tolerance", T(1.0e-5)),
        max_iterations = option(scenario, "pressure_max_iterations", 640),
    )
    solver.projection_iterations = iterations
    solver.projection_warning = converged ? "" : "pressure CG did not converge"
    converged || throw(NumericalFailure(
        :projection_failure,
        solver.projection_warning,
        :projection,
        Dict{String,Any}(
            "iterations" => iterations,
            "tolerance" => option(scenario, "pressure_tolerance", T(1.0e-5)),
            "relative_residual" => relative_residual,
        ),
    ))
    return u, v, relative_residual
end

function _pic_advect_particles!(
    solver::PicFlipSolver{T},
    start_control::ControlState,
    control::ControlState,
    timestep::T,
    initial_velocity::AbstractMatrix{T},
    u::AbstractMatrix{T},
    v::AbstractMatrix{T},
) where {T}
    scenario, _ = _pic_require(solver)
    start_positions = copy(solver.positions)
    midpoint = solver.positions .+ T(0.5) * timestep .* initial_velocity
    midpoint_velocity = faces_to_particle(u, v, midpoint, scenario.domain)
    solver.positions .+= timestep .* midpoint_velocity
    _pic_resolve_swept!(solver, start_positions, start_control, control)
    x0, x1 = scenario.domain.bounds[1]
    y0, y1 = scenario.domain.bounds[2]
    periodic_x = :x in scenario.domain.periodic_axes
    periodic_y = :y in scenario.domain.periodic_axes
    for particle in axes(solver.positions, 2)
        x = solver.positions[1, particle]
        y = solver.positions[2, particle]
        periodic_x && (x = x0 + mod(x - x0, x1 - x0))
        periodic_y && (y = y0 + mod(y - y0, y1 - y0))
        escaped = (!periodic_x && !(x0 <= x <= x1)) || (!periodic_y && !(y0 <= y <= y1))
        if escaped
            x = periodic_x ? x0 + T(next_float32!(solver.rng)) * (x1 - x0) :
                x0 + T(0.5) * T(next_float32!(solver.rng)) * dx(scenario.domain)
            y = y0 + T(next_float32!(solver.rng)) * (y1 - y0)
            solver.particle_velocity[:, particle] .= scenario.freestream
        end
        solver.positions[1, particle] = x
        solver.positions[2, particle] = y
    end
    _pic_resolve_collisions!(solver, control)
    return nothing
end

function _pic_maintain_population!(solver::PicFlipSolver{T}, control::ControlState) where {T}
    scenario, _ = _pic_require(solver)
    identifiers = particle_cell_ids(solver.positions, scenario.domain)
    counts = vec(particle_cell_counts(solver.positions, scenario.domain))
    desired = copy(counts)
    fluid_ids = Int[]
    for j in 1:ny(scenario.domain), i in 1:nx(scenario.domain)
        identifier = i + (j - 1) * nx(scenario.domain)
        if solver.solid[i, j]
            desired[identifier] = 0
        else
            push!(fluid_ids, identifier)
            counts[identifier] < 2 && (desired[identifier] = 4)
            counts[identifier] > 8 && (desired[identifier] = 4)
        end
    end
    difference = length(identifiers) - sum(desired)
    if difference > 0
        priority = sort(fluid_ids; by = identifier -> (desired[identifier], identifier))
        while difference > 0
            changed = false
            for identifier in priority
                desired[identifier] >= 8 && continue
                desired[identifier] += 1
                difference -= 1
                changed = true
                difference == 0 && break
            end
            changed || throw(ArgumentError("particle population exceeds maximum capacity"))
        end
    elseif difference < 0
        priority = sort(fluid_ids; by = identifier -> (-desired[identifier], identifier))
        while difference < 0
            changed = false
            for identifier in priority
                desired[identifier] <= 2 && continue
                desired[identifier] -= 1
                difference += 1
                changed = true
                difference == 0 && break
            end
            changed || throw(ArgumentError("particle population cannot satisfy minimum capacity"))
        end
    end
    ranks = zeros(Int, length(counts))
    donors = Int[]
    for particle in eachindex(identifiers)
        identifier = identifiers[particle]
        ranks[identifier] += 1
        ranks[identifier] > desired[identifier] && push!(donors, particle)
    end
    destinations = Int[]
    for identifier in eachindex(counts)
        for _ in 1:max(desired[identifier] - counts[identifier], 0)
            push!(destinations, identifier)
        end
    end
    length(donors) == length(destinations) ||
        throw(ArgumentError("particle population redistribution is unbalanced"))
    for (particle, identifier) in zip(donors, destinations)
        i = mod1(identifier, nx(scenario.domain))
        j = (identifier - 1) ÷ nx(scenario.domain) + 1
        jitter_x = T(next_float32!(solver.rng))
        jitter_y = T(next_float32!(solver.rng))
        solver.positions[1, particle] = scenario.domain.bounds[1][1] +
            (T(i - 1) + T(0.4) + T(0.2) * jitter_x) * dx(scenario.domain)
        solver.positions[2, particle] = scenario.domain.bounds[2][1] +
            (T(j - 1) + T(0.4) + T(0.2) * jitter_y) * dy(scenario.domain)
    end
    if !isempty(donors)
        u, v = cell_to_faces(solver.grid_velocity)
        solver.particle_velocity[:, donors] .= faces_to_particle(
            u,
            v,
            view(solver.positions, :, donors),
            scenario.domain,
        )
        solver.reseeded_last_step += length(donors)
        _pic_resolve_collisions!(solver, control)
    end
    return nothing
end

function advance!(solver::PicFlipSolver{T}, control::ControlState, target_dt::Real) where {T}
    scenario, geometry = _pic_require(solver)
    target = validate_advance_request(solver.time, control, target_dt)
    all(isfinite, solver.positions) && all(isfinite, solver.particle_velocity) &&
        all(isfinite, solver.grid_velocity) || throw(NumericalFailure(
            :nonfinite_state,
            "PIC/FLIP input state is non-finite",
            :postcondition,
        ))
    maximum_speed = max(
        maximum(hypot(solver.grid_velocity[i, j, 1], solver.grid_velocity[i, j, 2]) for
            i in axes(solver.grid_velocity, 1), j in axes(solver.grid_velocity, 2)),
        abs(scenario.freestream[1]),
        T(1.0e-6),
    )
    boundary_angular_velocity = T(control.angular_velocity_degrees)
    pose_sweep_angular_velocity =
        (T(control.angle_degrees) - solver.control.angle_degrees) / target
    radius = hypot(T(0.75) * scenario.foil.chord,
        (maximum_camber(geometry) + T(0.51) * thickness(geometry)) * scenario.foil.chord)
    wall_speed = radius * max(
        abs(T(deg2rad(boundary_angular_velocity))),
        abs(T(deg2rad(pose_sweep_angular_velocity))),
    )
    transport_speed = max(maximum_speed, wall_speed)
    stable_dt = solver.cfl * min(dx(scenario.domain), dy(scenario.domain)) / max(transport_speed, T(1.0e-6))
    substeps = max(1, ceil(Int, target / stable_dt))
    substeps <= 512 || throw(NumericalFailure(
        :stability_limit,
        "PIC/FLIP motion requires too many internal substeps",
        Symbol("particle-advection"),
        Dict{String,Any}(
            "required_substeps" => substeps,
            "maximum_substeps" => 512,
            "maximum_particle_speed" => maximum_speed,
            "maximum_wall_speed" => wall_speed,
        ),
    ))
    timestep = target / T(substeps)
    checkpoint = (
        copy(solver.positions), copy(solver.particle_velocity), copy(solver.grid_velocity),
        copy(solver.solid), solver.control, solver.time, solver.settling_steps,
        solver.rng.state, solver.rng.increment, solver.projection_warning,
        solver.reseeded_last_step, solver.swept_collisions_last_step,
        solver.advance_count, solver.projection_iterations, solver.solid_angle,
        solver.unsupported_face_fraction, solver.revision,
    )
    start_time = solver.time
    start_angle = solver.control.angle_degrees
    counts = Int[]
    projected_u, projected_v = cell_to_faces(solver.grid_velocity)
    pressure_residual = zero(T)
    diffusion_residual = zero(T)
    viscosity_iterations = 0
    final_speed = transport_speed
    particle_speed = maximum_speed
    maximum_particle_cfl = timestep * transport_speed /
        min(dx(scenario.domain), dy(scenario.domain))
    try
        solver.reseeded_last_step = 0
        solver.swept_collisions_last_step = 0
        for substep in 1:substeps
            fraction = T(substep) / T(substeps)
            sub_control = ControlState(
                start_time + fraction * target,
                start_angle + fraction * (T(control.angle_degrees) - start_angle),
                boundary_angular_velocity,
            )
            if solver.solid_angle != sub_control.angle_degrees
                solver.solid = solid_mask(geometry, scenario.domain, sub_control.angle_degrees)
                solver.solid_angle = sub_control.angle_degrees
            end
            start_control = solver.control
            _pic_resolve_collisions!(solver, sub_control)
            fallback_u, fallback_v = cell_to_faces(solver.grid_velocity)
            transferred_u, transferred_v, solver.unsupported_face_fraction = particle_to_faces(
                solver.positions,
                solver.particle_velocity,
                scenario.domain,
                fallback_u,
                fallback_v,
            )
            before_projection_u = copy(transferred_u)
            before_projection_v = copy(transferred_v)
            viscosity = reference_speed(scenario) * scenario.foil.chord / solver.reynolds_value
            diffused_u, u_iterations, u_residual, u_converged = implicit_diffuse_scalar(
                transferred_u, viscosity, timestep, scenario.domain;
                tolerance = option(scenario, "pressure_tolerance", T(1.0e-5)),
                max_iterations = option(scenario, "pressure_max_iterations", 640),
            )
            diffused_v, v_iterations, v_residual, v_converged = implicit_diffuse_scalar(
                transferred_v, viscosity, timestep, scenario.domain;
                tolerance = option(scenario, "pressure_tolerance", T(1.0e-5)),
                max_iterations = option(scenario, "pressure_max_iterations", 640),
            )
            diffusion_residual = max(u_residual, v_residual)
            viscosity_iterations = max(u_iterations, v_iterations)
            u_converged && v_converged || throw(NumericalFailure(
                :convergence_failure,
                "PIC/FLIP implicit viscosity did not converge",
                :viscosity,
                Dict{String,Any}(
                    "iterations" => viscosity_iterations,
                    "tolerance" => option(scenario, "pressure_tolerance", T(1.0e-5)),
                    "final_residual" => diffusion_residual,
                ),
            ))
            projected_u, projected_v, pressure_residual = _pic_project_faces!(
                solver, diffused_u, diffused_v, sub_control, timestep,
            )
            solver.grid_velocity = faces_to_cell(projected_u, projected_v)
            pic_velocity = faces_to_particle(
                projected_u, projected_v, solver.positions, scenario.domain,
            )
            delta = faces_to_particle(
                projected_u .- before_projection_u,
                projected_v .- before_projection_v,
                solver.positions,
                scenario.domain,
            )
            blend = solver.settling_steps > 0 ? zero(T) : solver.blend
            solver.particle_velocity .= (one(T) - blend) .* pic_velocity .+
                blend .* (solver.particle_velocity .+ delta)
            solver.control = sub_control
            _pic_advect_particles!(
                solver, start_control, sub_control, timestep, pic_velocity,
                projected_u, projected_v,
            )
            solver.settling_steps > 0 && (solver.settling_steps -= 1)
        end
        solver.advance_count += 1
        solver.advance_count % solver.population_interval == 0 &&
            _pic_maintain_population!(solver, control)
        _pic_resolve_collisions!(solver, control)
        all(isfinite, solver.positions) && all(isfinite, solver.particle_velocity) &&
            all(isfinite, solver.grid_velocity) || throw(NumericalFailure(
                :nonfinite_state,
                "PIC/FLIP produced non-finite state",
                :postcondition,
            ))
        identifiers = particle_cell_ids(solver.positions, scenario.domain)
        all((1 .<= identifiers) .&
            (identifiers .<= nx(scenario.domain) * ny(scenario.domain))) ||
            throw(NumericalFailure(
                :postcondition_failure,
                "PIC/FLIP particle escaped the domain",
                :postcondition,
            ))
        counts = vec(particle_cell_counts(solver.positions, scenario.domain))
        grid_speed = maximum(
            hypot(solver.grid_velocity[i, j, 1], solver.grid_velocity[i, j, 2]) for
                i in axes(solver.grid_velocity, 1), j in axes(solver.grid_velocity, 2)
        )
        particle_speed = maximum(
            hypot(solver.particle_velocity[1, index], solver.particle_velocity[2, index])
                for index in axes(solver.particle_velocity, 2)
        )
        final_speed = max(transport_speed, grid_speed, particle_speed)
        maximum_particle_cfl = timestep * final_speed /
            min(dx(scenario.domain), dy(scenario.domain))
        maximum_particle_cfl <= solver.cfl * (one(T) + T(1.0e-6)) ||
            throw(NumericalFailure(
                :stability_limit,
                "PIC/FLIP post-step motion exceeded its swept envelope",
                Symbol("particle-advection"),
                Dict{String,Any}(
                    "accepted_cfl" => maximum_particle_cfl,
                    "maximum_cfl" => solver.cfl,
                ),
            ))
    catch
        solver.positions, solver.particle_velocity, solver.grid_velocity, solver.solid,
            solver.control, solver.time, solver.settling_steps, rng_state, rng_increment,
            solver.projection_warning, solver.reseeded_last_step,
            solver.swept_collisions_last_step, solver.advance_count,
            solver.projection_iterations, solver.solid_angle,
            solver.unsupported_face_fraction, solver.revision = checkpoint
        solver.rng.state = rng_state
        solver.rng.increment = rng_increment
        rethrow()
    end
    solver.time = T(control.time)
    solver.control = ControlState(
        solver.time, T(control.angle_degrees), T(control.angular_velocity_degrees),
    )
    solver.revision += 1
    warnings = isempty(solver.projection_warning) ? String[] : [solver.projection_warning]
    fluid_counts = Int[]
    for j in axes(solver.solid, 2), i in axes(solver.solid, 1)
        solver.solid[i, j] || push!(fluid_counts, counts[(j - 1) * nx(scenario.domain) + i])
    end
    final_wall = wall_velocity_grid(geometry, scenario.domain, solver.control)
    return StepReport(
        target, target, substeps, final_speed, warnings, solver.revision,
        Dict{String,Any}(
            "maximum_particle_speed" => particle_speed,
            "maximum_wall_speed" => wall_speed,
            "maximum_particle_cfl" => maximum_particle_cfl,
            "maximum_characteristic_displacement" => maximum_particle_cfl,
            "particle_count" => size(solver.positions, 2),
            "empty_cell_fraction" => count(==(0), fluid_counts) / max(1, length(fluid_counts)),
            "underfilled_cell_fraction" => count(value -> value < 4, fluid_counts) /
                max(1, length(fluid_counts)),
            "unresolved_solid_particles" => 0,
            "minimum_particles_per_cell" => isempty(counts) ? 0 : minimum(counts),
            "maximum_particles_per_cell" => isempty(counts) ? 0 : maximum(counts),
            "unsupported_face_fraction" => solver.unsupported_face_fraction,
            "pressure_converged" => true,
            "pressure_iterations" => solver.projection_iterations,
            "pressure_relative_residual" => pressure_residual,
            "viscosity_converged" => true,
            "viscosity_iterations" => viscosity_iterations,
            "viscosity_final_residual" => diffusion_residual,
            "divergence_linf" => native_divergence_linf(
                projected_u, projected_v, scenario.domain, solver.solid,
            ),
            "solid_leakage" => solid_face_leakage(
                projected_u, projected_v, solver.solid, final_wall,
            ),
            "requested_reynolds" => solver.reynolds_value,
            "effective_reynolds" => solver.reynolds_value,
            "degraded_motion" => wall_speed == zero(T) &&
                abs(T(control.angle_degrees) - start_angle) > T(1.0e-9),
            "projection_iterations" => solver.projection_iterations,
        ),
    )
end

function sample_velocity(solver::PicFlipSolver{T}, points::AbstractMatrix{T}) where {T}
    scenario, _ = _pic_require(solver)
    return sample_velocity_field(solver.grid_velocity, points, scenario.domain)
end

function export_state(solver::PicFlipSolver{T}) where {T}
    scenario, _ = _pic_require(solver)
    velocity = copy(solver.grid_velocity)
    for index in CartesianIndices(solver.solid)
        solver.solid[index] || continue
        velocity[index, 1] = zero(T)
        velocity[index, 2] = zero(T)
    end
    return CanonicalFlowState(
        1,
        scenario.domain.bounds,
        scenario.domain.resolution,
        scenario.domain.periodic_axes,
        solver.time,
        solver.control.angle_degrees,
        solver.control.angular_velocity_degrees,
        "julia",
        solver_info(solver).id,
        cell_to_canonical(velocity),
    )
end

function import_state!(
    solver::PicFlipSolver{T},
    state::CanonicalFlowState{2,S},
    control::ControlState,
) where {T,S}
    scenario, geometry = _pic_require(solver)
    checkpoint = (
        copy(solver.positions), copy(solver.particle_velocity), copy(solver.grid_velocity),
        copy(solver.solid), solver.control, solver.time, solver.settling_steps,
        solver.rng.state, solver.rng.increment, solver.solid_angle,
        solver.unsupported_face_fraction, solver.revision,
    )
    try
        validate_canonical_import(state, scenario, control)
        velocity = T.(canonical_to_cell(state))
        solver.time = T(state.time)
        solver.control = ControlState(
            T(control.time), T(control.angle_degrees), T(control.angular_velocity_degrees),
        )
        solver.solid = solid_mask(geometry, scenario.domain, solver.control.angle_degrees)
        solver.solid_angle = solver.control.angle_degrees
        wall = wall_velocity_grid(geometry, scenario.domain, solver.control)
        for index in CartesianIndices(solver.solid)
            solver.solid[index] || continue
            velocity[index, 1] = wall[index, 1]
            velocity[index, 2] = wall[index, 2]
        end
        solver.grid_velocity = velocity
        _pic_seed_particles!(solver)
        solver.settling_steps = 1
    catch failure
        solver.positions, solver.particle_velocity, solver.grid_velocity, solver.solid,
            solver.control, solver.time, solver.settling_steps, rng_state, rng_increment,
            solver.solid_angle, solver.unsupported_face_fraction, solver.revision = checkpoint
        solver.rng.state = rng_state
        solver.rng.increment = rng_increment
        failure isa NumericalFailure || rethrow()
        return ImportOutcome(
            :rejected,
            failure.reason;
            warnings = [sprint(showerror, failure)],
            stage = failure.stage,
            evidence = failure.evidence,
        )
    end
    solver.revision += 1
    report = ImportReport(
        state.source_solver,
        solver_info(solver).id,
        ["solver particles", "FLIP velocity delta history"],
        ["The first imported step is PIC-dominant while FLIP history is rebuilt."],
    )
    return ImportOutcome(:accepted, :none; report, warnings = copy(report.warnings))
end

function _pic_percentile(values::Vector{Int}, fraction::Float64)
    isempty(values) && return 0.0
    sorted = sort(values)
    index = clamp(round(Int, 1 + fraction * (length(sorted) - 1)), 1, length(sorted))
    return Float64(sorted[index])
end

function diagnostics(solver::PicFlipSolver)
    scenario, geometry = _pic_require(solver)
    counts = particle_cell_counts(solver.positions, scenario.domain)
    fluid_counts = Int[counts[i, j] for j in 1:ny(scenario.domain), i in 1:nx(scenario.domain) if !solver.solid[i, j]]
    inside = count(particle -> begin
        point = SVector(solver.positions[1, particle], solver.positions[2, particle])
        signed_distance(geometry, point, solver.control.angle_degrees) <= 0
    end, axes(solver.positions, 2))
    values = Dict{String,Float64}(
        "time" => Float64(solver.time),
        "requested_reynolds" => Float64(solver.reynolds_value),
        "kinetic_energy" => Float64(kinetic_energy(solver.grid_velocity)),
        "enstrophy" => Float64(enstrophy(solver.grid_velocity, scenario.domain)),
        "divergence_l2" => Float64(divergence_l2(solver.grid_velocity, scenario.domain)),
        "solid_leakage" => Float64(solid_leakage(solver.grid_velocity, solver.solid)),
        "particle_count" => Float64(size(solver.positions, 2)),
        "unsupported_face_fraction" => Float64(solver.unsupported_face_fraction),
        "empty_fluid_cell_fraction" => count(==(0), fluid_counts) / length(fluid_counts),
        "underfilled_fluid_cell_fraction" => count(<(2), fluid_counts) / length(fluid_counts),
        "p05_particles_per_fluid_cell" => _pic_percentile(fluid_counts, 0.05),
        "p95_particles_per_fluid_cell" => _pic_percentile(fluid_counts, 0.95),
        "max_particles_per_fluid_cell" => Float64(maximum(fluid_counts)),
        "reseeded_last_step" => Float64(solver.reseeded_last_step),
        "swept_collisions_last_step" => Float64(solver.swept_collisions_last_step),
        "particles_inside_solid" => Float64(inside),
        "wake_width" => Float64(wake_width(solver.grid_velocity, scenario.domain, scenario.foil.pivot[1])),
        "recirculation_area" => Float64(recirculation_area(solver.grid_velocity, scenario.domain, scenario.foil.pivot[1])),
        "projection_iterations" => Float64(solver.projection_iterations),
    )
    all(isfinite, Base.values(values)) || throw(NumericalFailure(
        :nonfinite_state,
        "PIC/FLIP produced non-finite diagnostics",
    ))
    warnings = isempty(solver.projection_warning) ? String[] : [solver.projection_warning]
    return Diagnostics(values, warnings, solver.revision)
end
