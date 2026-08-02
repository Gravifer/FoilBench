const LBM_D2Q9_INFO = SolverInfo(
    "lbm-d2q9",
    "D2Q9 TRT LBM",
    (2,),
    true,
    :julia_cpu,
)

mutable struct LBMSolver{T<:AbstractFloat} <: AbstractFlowSolver{2,T}
    scenario::Union{Nothing,Scenario{2,T}}
    geometry::Union{Nothing,NacaFoil{2,T}}
    populations::Array{T,3}
    outlet::Matrix{T}
    sponge::Matrix{T}
    solid::BitMatrix
    centers::Array{T,3}
    distance::Matrix{T}
    boundary_equilibrium::Array{T,3}
    control::ControlState{T}
    time::T
    reynolds_value::T
    reference_speed::T
    scaling::LBMScaling{T}
    solid_angle::T
    density_initial::T
end

function LBMSolver(::Type{T} = Float32) where {T<:AbstractFloat}
    empty_scaling = LBMScaling(one(T), T(0.08), T(0.01), one(T), one(T), one(T), false)
    return LBMSolver{T}(
        nothing,
        nothing,
        Array{T,3}(undef, 9, 0, 0),
        Matrix{T}(undef, 9, 0),
        Matrix{T}(undef, 0, 0),
        falses(0, 0),
        Array{T,3}(undef, 0, 0, 2),
        Matrix{T}(undef, 0, 0),
        Array{T,3}(undef, 9, 0, 0),
        ControlState(zero(T), zero(T), zero(T)),
        zero(T),
        one(T),
        one(T),
        empty_scaling,
        T(NaN),
        one(T),
    )
end

solver_info(::LBMSolver) = LBM_D2Q9_INFO
reynolds(solver::LBMSolver) = solver.reynolds_value

function _lbm_require(solver::LBMSolver)
    solver.scenario === nothing && throw(ArgumentError("D2Q9 LBM is not initialized"))
    solver.geometry === nothing && throw(ArgumentError("D2Q9 LBM is not initialized"))
    return solver.scenario, solver.geometry
end

function set_reynolds!(solver::LBMSolver{T}, selected::Real) where {T}
    isfinite(selected) && selected > 0 ||
        throw(ArgumentError("Reynolds number must be finite and positive"))
    solver.reynolds_value = T(selected)
    solver.scenario === nothing || (solver.scaling = lbm_scaling(solver.scenario, selected))
    return nothing
end

function _lbm_distance(
    geometry::NacaFoil{2,T},
    centers::AbstractArray{T,3},
    angle::Real,
) where {T}
    distance = Matrix{T}(undef, size(centers, 1), size(centers, 2))
    for j in axes(distance, 2), i in axes(distance, 1)
        point = SVector{2,T}(centers[i, j, 1], centers[i, j, 2])
        distance[i, j] = signed_distance(geometry, point, angle)
    end
    return distance
end

function _lbm_initial_velocity(scenario::Scenario{2,T}, speed::T) where {T}
    centers = cell_centers(scenario.domain)
    velocity = Array{T,3}(undef, nx(scenario.domain), ny(scenario.domain), 2)
    scale = speed / reference_speed(scenario)
    velocity[:, :, 1] .= scenario.freestream[1] * scale
    velocity[:, :, 2] .= scenario.freestream[2] * scale
    initial = option(scenario, "initial_condition", "freestream")
    if initial == "taylor-green"
        for j in axes(velocity, 2), i in axes(velocity, 1)
            x = centers[i, j, 1]
            y = centers[i, j, 2]
            velocity[i, j, 1] = speed * sin(x) * cos(y)
            velocity[i, j, 2] = -speed * cos(x) * sin(y)
        end
    elseif initial == "poiseuille"
        y0, y1 = scenario.domain.bounds[2]
        radius = T(0.5) * (y1 - y0)
        center = T(0.5) * (y0 + y1)
        for j in axes(velocity, 2), i in axes(velocity, 1)
            normalized = (centers[i, j, 2] - center) / radius
            velocity[i, j, 1] = speed * T(1.5) * (one(T) - normalized^2)
            velocity[i, j, 2] = zero(T)
        end
    elseif initial != "freestream"
        throw(ArgumentError("unsupported LBM initial condition: $initial"))
    end
    return centers, velocity
end

function _lbm_sponge(scenario::Scenario{2,T}) where {T}
    sponge = zeros(T, nx(scenario.domain), ny(scenario.domain))
    width = max(3, min(nx(scenario.domain), ny(scenario.domain)) ÷ 16)
    if !(:y in scenario.domain.periodic_axes)
        for j in 1:ny(scenario.domain)
            distance = min(j - 1, ny(scenario.domain) - j)
            strength = T(0.12) * clamp(T(width - distance) / T(width), zero(T), one(T))^2
            for i in 1:nx(scenario.domain)
                sponge[i, j] = max(sponge[i, j], strength)
            end
        end
    end
    if !(:x in scenario.domain.periodic_axes)
        outlet_width = 2 * width
        for i in 1:nx(scenario.domain)
            distance = nx(scenario.domain) - i
            strength = T(0.08) *
                clamp(T(outlet_width - distance) / T(outlet_width), zero(T), one(T))^2
            for j in 1:ny(scenario.domain)
                sponge[i, j] = max(sponge[i, j], strength)
            end
        end
    end
    return sponge
end

function initialize!(
    solver::LBMSolver{T},
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
    solver.reference_speed = reference_speed(scenario)
    solver.reynolds_value = scenario.reynolds
    solver.scaling = lbm_scaling(scenario)
    solver.centers, velocity = _lbm_initial_velocity(scenario, solver.scaling.lattice_speed)
    density = ones(T, nx(scenario.domain), ny(scenario.domain))
    solver.populations = lbm_equilibrium(density, velocity)
    solver.outlet = copy(view(solver.populations, :, nx(scenario.domain), :))
    solver.distance = _lbm_distance(geometry, solver.centers, solver.control.angle_degrees)
    solver.solid = solver.distance .<= zero(T)
    solver.solid_angle = solver.control.angle_degrees
    target = zeros(T, nx(scenario.domain), ny(scenario.domain), 2)
    target[:, :, 1] .= scenario.freestream[1] * solver.scaling.lattice_speed / solver.reference_speed
    target[:, :, 2] .= scenario.freestream[2] * solver.scaling.lattice_speed / solver.reference_speed
    solver.boundary_equilibrium = lbm_equilibrium(density, target)
    solver.sponge = _lbm_sponge(scenario)
    solver.density_initial = one(T)
    return nothing
end

function initialize!(
    solver::LBMSolver{T},
    scenario::Scenario{D,T},
    geometry::NacaFoil{D,T},
    seed::Integer,
) where {D,T}
    require_supported(solver_info(solver), scenario)
    error("unreachable D2Q9 LBM dimension $D for $(typeof(geometry)) and seed $seed")
end

function _lbm_physical_velocity(solver::LBMSolver{T}) where {T}
    _, lattice_velocity = lbm_macroscopic(solver.populations)
    return lattice_velocity .* (solver.reference_speed / solver.scaling.lattice_speed)
end

cell_velocity(solver::LBMSolver) = _lbm_physical_velocity(solver)

function _lbm_update_solid!(solver::LBMSolver{T}, control::ControlState) where {T}
    scenario, geometry = _lbm_require(solver)
    T(control.angle_degrees) == solver.solid_angle && return nothing
    distance = _lbm_distance(geometry, solver.centers, control.angle_degrees)
    new_solid = distance .<= zero(T)
    uncovered = solver.solid .& .!new_solid
    if any(uncovered)
        density, velocity = lbm_macroscopic(solver.populations)
        target_x = scenario.freestream[1] * solver.scaling.lattice_speed / solver.reference_speed
        target_y = scenario.freestream[2] * solver.scaling.lattice_speed / solver.reference_speed
        for j in axes(uncovered, 2), i in axes(uncovered, 1)
            uncovered[i, j] || continue
            density[i, j] = one(T)
            velocity[i, j, 1] = target_x
            velocity[i, j, 2] = target_y
        end
        equilibrium = lbm_equilibrium(density, velocity)
        for j in axes(uncovered, 2), i in axes(uncovered, 1)
            uncovered[i, j] || continue
            solver.populations[:, i, j] .= equilibrium[:, i, j]
        end
    end
    solver.distance = distance
    solver.solid = new_solid
    solver.solid_angle = T(control.angle_degrees)
    return nothing
end

function _lbm_left_boundary!(f::AbstractArray{T,3}, ux::T, uy::T) where {T}
    for j in axes(f, 3)
        density = (f[1, 1, j] + f[3, 1, j] + f[5, 1, j] +
            T(2) * (f[4, 1, j] + f[7, 1, j] + f[8, 1, j])) / (one(T) - ux)
        f[2, 1, j] = f[4, 1, j] + T(2 / 3) * density * ux
        f[6, 1, j] = f[8, 1, j] + T(0.5) * (f[5, 1, j] - f[3, 1, j]) +
            T(1 / 6) * density * ux + T(0.5) * density * uy
        f[9, 1, j] = f[7, 1, j] + T(0.5) * (f[3, 1, j] - f[5, 1, j]) +
            T(1 / 6) * density * ux - T(0.5) * density * uy
    end
    return nothing
end

function _lbm_bottom_boundary!(f::AbstractArray{T,3}, ux::T, uy::T) where {T}
    for i in axes(f, 2)
        density = (f[1, i, 1] + f[2, i, 1] + f[4, i, 1] +
            T(2) * (f[5, i, 1] + f[8, i, 1] + f[9, i, 1])) / (one(T) - uy)
        f[3, i, 1] = f[5, i, 1] + T(2 / 3) * density * uy
        f[6, i, 1] = f[8, i, 1] + T(0.5) * (f[4, i, 1] - f[2, i, 1]) +
            T(1 / 6) * density * uy + T(0.5) * density * ux
        f[7, i, 1] = f[9, i, 1] + T(0.5) * (f[2, i, 1] - f[4, i, 1]) +
            T(1 / 6) * density * uy - T(0.5) * density * ux
    end
    return nothing
end

function _lbm_top_boundary!(f::AbstractArray{T,3}, ux::T, uy::T) where {T}
    j = size(f, 3)
    for i in axes(f, 2)
        density = (f[1, i, j] + f[2, i, j] + f[4, i, j] +
            T(2) * (f[3, i, j] + f[6, i, j] + f[7, i, j])) / (one(T) + uy)
        f[5, i, j] = f[3, i, j] - T(2 / 3) * density * uy
        f[8, i, j] = f[6, i, j] + T(0.5) * (f[2, i, j] - f[4, i, j]) -
            T(1 / 6) * density * uy - T(0.5) * density * ux
        f[9, i, j] = f[7, i, j] + T(0.5) * (f[4, i, j] - f[2, i, j]) -
            T(1 / 6) * density * uy + T(0.5) * density * ux
    end
    return nothing
end

function _lbm_neighbor(index::Int, delta::Int, count::Int, periodic::Bool)
    selected = index + delta
    periodic && return mod1(selected, count), true
    return selected, 1 <= selected <= count
end

function _lbm_stream!(
    solver::LBMSolver{T},
    post::AbstractArray{T,3},
    density::AbstractMatrix{T},
    control::ControlState,
) where {T}
    scenario, _ = _lbm_require(solver)
    streamed = zeros(T, size(post))
    periodic_x = :x in scenario.domain.periodic_axes
    periodic_y = :y in scenario.domain.periodic_axes
    omega = T(deg2rad(control.angular_velocity_degrees))
    wall_scale = solver.scaling.lattice_speed / solver.reference_speed
    for j in 1:ny(scenario.domain), i in 1:nx(scenario.domain)
        solver.solid[i, j] && continue
        streamed[1, i, j] = post[1, i, j]
        for direction in 2:9
            cx = Int(D2Q9_C[1, direction])
            cy = Int(D2Q9_C[2, direction])
            destination_i, valid_x = _lbm_neighbor(i, cx, nx(scenario.domain), periodic_x)
            destination_j, valid_y = _lbm_neighbor(j, cy, ny(scenario.domain), periodic_y)
            valid_x && valid_y || continue
            if !solver.solid[destination_i, destination_j]
                streamed[direction, destination_i, destination_j] += post[direction, i, j]
                continue
            end
            source_distance = solver.distance[i, j]
            destination_distance = solver.distance[destination_i, destination_j]
            fraction = clamp(
                source_distance / max(source_distance - destination_distance, T(1.0e-12)),
                T(0.05),
                one(T),
            )
            opposite = Int(D2Q9_OPPOSITE[direction])
            reflected = if fraction < T(0.5)
                upstream_i, upstream_x = _lbm_neighbor(i, -cx, nx(scenario.domain), periodic_x)
                upstream_j, upstream_y = _lbm_neighbor(j, -cy, ny(scenario.domain), periodic_y)
                upstream = upstream_x && upstream_y ? post[direction, upstream_i, upstream_j] :
                    post[direction, i, j]
                T(2) * fraction * post[direction, i, j] + (one(T) - T(2) * fraction) * upstream
            else
                post[direction, i, j] / (T(2) * fraction) +
                    (T(2) * fraction - one(T)) * post[opposite, i, j] / (T(2) * fraction)
            end
            wall_x = solver.centers[i, j, 1] + fraction * T(cx) * dx(scenario.domain)
            wall_y = solver.centers[i, j, 2] + fraction * T(cy) * dy(scenario.domain)
            relative_x = wall_x - scenario.foil.pivot[1]
            relative_y = wall_y - scenario.foil.pivot[2]
            wall_ux = -omega * relative_y * wall_scale
            wall_uy = omega * relative_x * wall_scale
            projection = T(cx) * wall_ux + T(cy) * wall_uy
            reflected -= T(6) * T(D2Q9_W[direction]) * density[i, j] * projection
            streamed[opposite, i, j] = reflected
        end
    end
    return streamed
end

function _lbm_apply_boundaries!(solver::LBMSolver{T}, streamed::Array{T,3}) where {T}
    scenario, _ = _lbm_require(solver)
    target_x = scenario.freestream[1] * solver.scaling.lattice_speed / solver.reference_speed
    target_y = scenario.freestream[2] * solver.scaling.lattice_speed / solver.reference_speed
    if !(:y in scenario.domain.periodic_axes)
        if option(scenario, "initial_condition", "") == "poiseuille"
            for i in 1:nx(scenario.domain), direction in 1:9
                opposite = Int(D2Q9_OPPOSITE[direction])
                streamed[direction, i, 1] = streamed[opposite, i, 2]
                streamed[direction, i, end] = streamed[opposite, i, end - 1]
            end
        else
            _lbm_bottom_boundary!(streamed, target_x, target_y)
            _lbm_top_boundary!(streamed, target_x, target_y)
        end
    end
    if !(:x in scenario.domain.periodic_axes)
        _lbm_left_boundary!(streamed, target_x, target_y)
        outlet_x = nx(scenario.domain)
        for j in 1:ny(scenario.domain), direction in 1:9
            previous = solver.outlet[direction, j]
            streamed[direction, outlet_x, j] = previous + solver.scaling.lattice_speed *
                (streamed[direction, outlet_x - 1, j] - previous)
        end
        solver.outlet .= view(streamed, :, outlet_x, :)
    end
    if !(:x in scenario.domain.periodic_axes) && !(:y in scenario.domain.periodic_axes)
        for (i, j) in ((1, 1), (1, ny(scenario.domain)),
            (nx(scenario.domain), 1), (nx(scenario.domain), ny(scenario.domain)))
            streamed[:, i, j] .= solver.boundary_equilibrium[:, i, j]
        end
    end
    for j in 1:ny(scenario.domain), i in 1:nx(scenario.domain)
        strength = solver.sponge[i, j]
        strength == zero(T) && continue
        for direction in 1:9
            streamed[direction, i, j] = (one(T) - strength) * streamed[direction, i, j] +
                strength * solver.boundary_equilibrium[direction, i, j]
        end
    end
    return nothing
end

function _lbm_step!(solver::LBMSolver{T}, control::ControlState) where {T}
    density, post = lbm_trt_collision(
        solver.populations,
        solver.scaling.omega_plus,
        solver.scaling.omega_minus,
    )
    streamed = _lbm_stream!(solver, post, density, control)
    _lbm_apply_boundaries!(solver, streamed)
    all(isfinite, streamed) || throw(NumericalFailure(
        :nonfinite_state,
        "D2Q9 LBM produced non-finite populations",
    ))
    solver.populations = streamed
    solver.control = ControlState(
        T(control.time),
        T(control.angle_degrees),
        T(control.angular_velocity_degrees),
    )
    return nothing
end

function advance!(solver::LBMSolver{T}, control::ControlState, target_dt::Real) where {T}
    _lbm_require(solver)
    target = T(target_dt)
    target > zero(T) || throw(ArgumentError("target_dt must be positive"))
    solver.scaling = lbm_scaling(solver.scenario, solver.reynolds_value)
    substeps = max(1, ceil(Int, target / solver.scaling.lattice_dt - T(1.0e-12)))
    for substep in 1:substeps
        fraction = T(substep) / T(substeps)
        sub_control = ControlState(
            solver.time + fraction * target,
            solver.control.angle_degrees +
                fraction * (T(control.angle_degrees) - solver.control.angle_degrees),
            T(control.angular_velocity_degrees),
        )
        _lbm_update_solid!(solver, sub_control)
        _lbm_step!(solver, sub_control)
    end
    solver.time += target
    solver.control = ControlState(solver.time, T(control.angle_degrees), T(control.angular_velocity_degrees))
    velocity = cell_velocity(solver)
    maximum_speed = maximum(hypot(velocity[i, j, 1], velocity[i, j, 2]) for
        i in axes(velocity, 1), j in axes(velocity, 2))
    warnings = solver.scaling.clamped ?
        ["LBM relaxation clamp active: effective Re=$(round(solver.scaling.effective_reynolds; digits = 1))"] :
        String[]
    return StepReport(target, target, substeps, maximum_speed, warnings)
end

function sample_velocity(solver::LBMSolver{T}, points::AbstractMatrix{T}) where {T}
    scenario, _ = _lbm_require(solver)
    return sample_velocity_field(cell_velocity(solver), points, scenario.domain)
end

function export_state(solver::LBMSolver{T}) where {T}
    scenario, _ = _lbm_require(solver)
    density, _ = lbm_macroscopic(solver.populations)
    canonical_density = Array{T,3}(undef, 1, ny(scenario.domain), nx(scenario.domain))
    for j in 1:ny(scenario.domain), i in 1:nx(scenario.domain)
        canonical_density[1, j, i] = density[i, j]
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
        cell_to_canonical(cell_velocity(solver)),
        canonical_density,
    )
end

function import_state!(
    solver::LBMSolver{T},
    state::CanonicalFlowState{2,S},
    control::ControlState,
) where {T,S}
    scenario, geometry = _lbm_require(solver)
    state.resolution == scenario.domain.resolution ||
        throw(DimensionMismatch("warm import requires the same 2D resolution"))
    physical = T.(canonical_to_cell(state))
    lattice = physical .* (solver.scaling.lattice_speed / solver.reference_speed)
    density = ones(T, nx(scenario.domain), ny(scenario.domain))
    if state.density !== nothing
        for j in 1:ny(scenario.domain), i in 1:nx(scenario.domain)
            density[i, j] = T(state.density[1, j, i])
        end
    end
    solver.populations = lbm_equilibrium(density, lattice)
    solver.outlet = copy(view(solver.populations, :, nx(scenario.domain), :))
    solver.time = T(state.time)
    solver.control = ControlState(T(control.time), T(control.angle_degrees), T(control.angular_velocity_degrees))
    solver.distance = _lbm_distance(geometry, solver.centers, solver.control.angle_degrees)
    solver.solid = solver.distance .<= zero(T)
    solver.solid_angle = solver.control.angle_degrees
    return ImportReport(
        state.source_solver,
        solver_info(solver).id,
        ["non-equilibrium lattice populations", "TRT kinetic modes"],
        ["LBM resumes from local equilibrium; an initialization transient is expected."],
    )
end

function diagnostics(solver::LBMSolver)
    scenario, _ = _lbm_require(solver)
    density, _ = lbm_macroscopic(solver.populations)
    velocity = cell_velocity(solver)
    values = Dict{String,Float64}(
        "time" => Float64(solver.time),
        "requested_reynolds" => Float64(solver.reynolds_value),
        "effective_reynolds" => Float64(solver.scaling.effective_reynolds),
        "kinetic_energy" => Float64(kinetic_energy(velocity)),
        "enstrophy" => Float64(enstrophy(velocity, scenario.domain)),
        "divergence_l2" => Float64(divergence_l2(velocity, scenario.domain)),
        "solid_leakage" => Float64(solid_leakage(velocity, solver.solid)),
        "density_mean" => Float64(sum(density) / length(density)),
        "density_drift" => Float64(sum(density) / length(density) - solver.density_initial),
        "wake_width" => Float64(wake_width(velocity, scenario.domain, scenario.foil.pivot[1])),
        "recirculation_area" => Float64(recirculation_area(velocity, scenario.domain, scenario.foil.pivot[1])),
    )
    all(isfinite, Base.values(values)) || throw(NumericalFailure(
        :nonfinite_state,
        "D2Q9 LBM produced non-finite diagnostics",
    ))
    warnings = solver.scaling.clamped ?
        ["LBM relaxation clamp active: effective Re=$(round(solver.scaling.effective_reynolds; digits = 1))"] :
        String[]
    return Diagnostics(values, warnings)
end
