const STABLE_FLUIDS_INFO = SolverInfo(
    "stable-fluids",
    "Stable Fluids (MAC)",
    (2,),
    true,
    :julia_cpu,
)

mutable struct StableFluidsSolver{T<:AbstractFloat} <: AbstractFlowSolver{2,T}
    scenario::Union{Nothing,Scenario{2,T}}
    geometry::Union{Nothing,NacaFoil{2,T}}
    u::Matrix{T}
    v::Matrix{T}
    solid::BitMatrix
    control::ControlState{T}
    time::T
    reynolds_value::T
    maccormack::Bool
    face_advection::Bool
    skew_rk2::Bool
    projection_iterations::Int
    diffusion_iterations::Int
end

function StableFluidsSolver(::Type{T} = Float32) where {T<:AbstractFloat}
    return StableFluidsSolver{T}(
        nothing,
        nothing,
        Matrix{T}(undef, 0, 0),
        Matrix{T}(undef, 0, 0),
        falses(0, 0),
        ControlState(zero(T), zero(T), zero(T)),
        zero(T),
        one(T),
        true,
        false,
        false,
        0,
        0,
    )
end

solver_info(::StableFluidsSolver) = STABLE_FLUIDS_INFO
reynolds(solver::StableFluidsSolver) = solver.reynolds_value

function stable_transport_mode(solver::StableFluidsSolver)
    solver.skew_rk2 && return "skew-rk2"
    return solver.maccormack ? "maccormack" : "semi-lagrangian"
end

function set_stable_transport_mode!(solver::StableFluidsSolver, mode::AbstractString)
    mode in ("maccormack", "semi-lagrangian", "skew-rk2") ||
        throw(ArgumentError("unsupported Stable Fluids advection: $mode"))
    solver.maccormack = mode == "maccormack"
    solver.skew_rk2 = mode == "skew-rk2"
    return stable_transport_mode(solver)
end

function set_reynolds!(solver::StableFluidsSolver{T}, selected::Real) where {T}
    isfinite(selected) && selected > 0 ||
        throw(ArgumentError("Reynolds number must be finite and positive"))
    solver.reynolds_value = T(selected)
    return nothing
end

function _stable_require(solver::StableFluidsSolver)
    solver.scenario === nothing && throw(ArgumentError("Stable Fluids is not initialized"))
    solver.geometry === nothing && throw(ArgumentError("Stable Fluids is not initialized"))
    return solver.scenario, solver.geometry
end

function _initial_velocity(scenario::Scenario{2,T}) where {T}
    velocity = Array{T,3}(undef, nx(scenario.domain), ny(scenario.domain), 2)
    velocity[:, :, 1] .= scenario.freestream[1]
    velocity[:, :, 2] .= scenario.freestream[2]
    initial = option(scenario, "initial_condition", "freestream")
    centers = cell_centers(scenario.domain)
    if initial == "taylor-green"
        for j in 1:ny(scenario.domain), i in 1:nx(scenario.domain)
            x = centers[i, j, 1]
            y = centers[i, j, 2]
            velocity[i, j, 1] = sin(x) * cos(y)
            velocity[i, j, 2] = -cos(x) * sin(y)
        end
    elseif initial == "poiseuille"
        y0, y1 = scenario.domain.bounds[2]
        radius = T(0.5) * (y1 - y0)
        center = T(0.5) * (y0 + y1)
        for j in 1:ny(scenario.domain), i in 1:nx(scenario.domain)
            normalized = (centers[i, j, 2] - center) / radius
            velocity[i, j, 1] = T(1.5) * (one(T) - normalized^2)
            velocity[i, j, 2] = zero(T)
        end
    elseif initial != "freestream"
        throw(ArgumentError("unsupported Stable Fluids initial condition: $initial"))
    end
    return velocity
end

function initialize!(
    solver::StableFluidsSolver{T},
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
    set_reynolds!(solver, scenario.reynolds)
    solver.u, solver.v = cell_to_faces(_initial_velocity(scenario))
    solver.solid = solid_mask(geometry, scenario.domain, solver.control.angle_degrees)
    advection = option(scenario, "stable_advection", "maccormack")
    advection in ("maccormack", "semi-lagrangian", "skew-rk2") ||
        throw(ArgumentError("unsupported Stable Fluids advection: $advection"))
    set_stable_transport_mode!(solver, advection)
    solver.face_advection = option(scenario, "stable_face_advection", false)
    solver.projection_iterations = 0
    solver.diffusion_iterations = 0
    _project!(solver, max(scenario.output_dt, T(1.0e-4)))
    return nothing
end

function initialize!(
    solver::StableFluidsSolver{T},
    scenario::Scenario{D,T},
    geometry::NacaFoil{D,T},
    seed::Integer,
) where {D,T}
    require_supported(solver_info(solver), scenario)
    error("unreachable Stable Fluids dimension $D for $(typeof(geometry)) and seed $seed")
end

function _maximum_speed(velocity::AbstractArray{T,3}) where {T}
    selected = zero(T)
    for j in axes(velocity, 2), i in axes(velocity, 1)
        selected = max(selected, hypot(velocity[i, j, 1], velocity[i, j, 2]))
    end
    return selected
end

function _project!(solver::StableFluidsSolver{T}, timestep::T) where {T}
    scenario, geometry = _stable_require(solver)
    all(isfinite, solver.u) && all(isfinite, solver.v) ||
        throw(NumericalFailure(
            :nonfinite_state,
            "Stable Fluids projection received non-finite velocity",
        ))
    wall = wall_velocity_grid(geometry, scenario.domain, solver.control)
    face_speed = max(maximum(abs, solver.u), maximum(abs, solver.v))
    wall_speed = any(solver.solid) ? _maximum_speed(wall) : zero(T)
    configured_cfl = option(scenario, "stable_cfl", T(0.7))
    solver.skew_rk2 && (configured_cfl = min(configured_cfl, T(0.4)))
    projection_limit = max(one(T), T(2) * configured_cfl)
    projection_cfl = max(face_speed, wall_speed) * timestep / min(dx(scenario.domain), dy(scenario.domain))
    projection_cfl <= projection_limit || throw(NumericalFailure(
        :excessive_velocity,
        "Stable Fluids projection CFL $projection_cfl exceeds $projection_limit",
    ))
    channel_walls = option(scenario, "initial_condition", "") == "poiseuille"
    tolerance = option(scenario, "pressure_tolerance", T(1.0e-5))
    max_iterations = option(scenario, "pressure_max_iterations", 640)
    iterations, converged = project_faces!(
        solver.u,
        solver.v,
        scenario.domain,
        solver.solid,
        wall,
        scenario.freestream,
        timestep;
        channel_walls,
        tolerance,
        max_iterations,
    )
    solver.projection_iterations = iterations
    converged || throw(NumericalFailure(
        :projection_failure,
        "Stable Fluids pressure CG did not converge",
    ))
    all(isfinite, solver.u) && all(isfinite, solver.v) ||
        throw(NumericalFailure(
            :nonfinite_state,
            "Stable Fluids projection produced non-finite velocity",
        ))
    return nothing
end

cell_velocity(solver::StableFluidsSolver) = faces_to_cell(solver.u, solver.v)

function _diffuse_faces!(
    solver::StableFluidsSolver{T},
    viscosity::T,
    timestep::T,
) where {T}
    scenario, _ = _stable_require(solver)
    tolerance = option(scenario, "pressure_tolerance", T(1.0e-5))
    solver.u, u_iterations, u_converged = implicit_diffuse_scalar(
        solver.u,
        viscosity,
        timestep,
        scenario.domain;
        tolerance,
    )
    solver.v, v_iterations, v_converged = implicit_diffuse_scalar(
        solver.v,
        viscosity,
        timestep,
        scenario.domain;
        tolerance,
    )
    solver.diffusion_iterations = max(u_iterations, v_iterations)
    u_converged && v_converged ||
        throw(NumericalFailure(
            :projection_failure,
            "Stable Fluids implicit viscosity did not converge",
        ))
    return nothing
end

function advance!(
    solver::StableFluidsSolver{T},
    control::ControlState,
    target_dt::Real,
) where {T}
    scenario, geometry = _stable_require(solver)
    target = T(target_dt)
    target > zero(T) || throw(ArgumentError("target_dt must be positive"))
    maximum_speed = max(_maximum_speed(cell_velocity(solver)), abs(scenario.freestream[1]), eps(T))
    cfl = option(scenario, "stable_cfl", T(0.7))
    solver.skew_rk2 && (cfl = min(cfl, T(0.4)))
    stable_dt = cfl * min(dx(scenario.domain), dy(scenario.domain)) / maximum_speed
    substeps = max(1, ceil(Int, target / stable_dt))
    timestep = target / T(substeps)
    viscosity = reference_speed(scenario) * scenario.foil.chord / solver.reynolds_value
    for substep in 1:substeps
        fraction = T(substep) / T(substeps)
        sub_control = ControlState(
            solver.time + fraction * target,
            solver.control.angle_degrees +
                fraction * (T(control.angle_degrees) - solver.control.angle_degrees),
            T(control.angular_velocity_degrees),
        )
        wall = wall_velocity_grid(geometry, scenario.domain, sub_control)
        if solver.skew_rk2
            solver.u, solver.v = advect_faces_skew_rk2(
                solver.u,
                solver.v,
                timestep,
                scenario.domain,
                solver.solid,
                wall,
                scenario.freestream,
            )
            _diffuse_faces!(solver, viscosity, timestep)
        elseif solver.face_advection
            solver.u, solver.v = advect_faces(
                solver.u,
                solver.v,
                timestep,
                scenario.domain;
                maccormack = solver.maccormack,
            )
            _diffuse_faces!(solver, viscosity, timestep)
        else
            velocity = advect_velocity(
                cell_velocity(solver),
                timestep,
                scenario.domain;
                maccormack = solver.maccormack,
            )
            velocity, diffusion_iterations, diffusion_converged = implicit_diffuse_velocity(
                velocity,
                viscosity,
                timestep,
                scenario.domain;
                tolerance = option(scenario, "pressure_tolerance", T(1.0e-5)),
            )
            diffusion_converged ||
                throw(NumericalFailure(
                    :projection_failure,
                    "Stable Fluids implicit viscosity did not converge",
                ))
            solver.diffusion_iterations = diffusion_iterations
            solver.u, solver.v = cell_to_faces(velocity)
        end
        solver.control = sub_control
        solver.solid = solid_mask(geometry, scenario.domain, sub_control.angle_degrees)
        _project!(solver, timestep)
    end
    solver.time += target
    solver.control = ControlState(
        solver.time,
        T(control.angle_degrees),
        T(control.angular_velocity_degrees),
    )
    return StepReport(target, target, substeps, maximum_speed, String[])
end

function sample_velocity(
    solver::StableFluidsSolver{T},
    points::AbstractMatrix{T},
) where {T}
    scenario, _ = _stable_require(solver)
    return sample_velocity_field(cell_velocity(solver), points, scenario.domain)
end

function export_state(solver::StableFluidsSolver{T}) where {T}
    scenario, _ = _stable_require(solver)
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
    )
end

function import_state!(
    solver::StableFluidsSolver{T},
    state::CanonicalFlowState{2,S},
    control::ControlState,
) where {T,S}
    scenario, geometry = _stable_require(solver)
    state.resolution == scenario.domain.resolution || return ImportOutcome(
        :rejected,
        :incompatible_domain;
        warnings = ["warm import requires the same 2D resolution"],
    )
    imported = T.(canonical_to_cell(state))
    solver.u, solver.v = cell_to_faces(imported)
    solver.time = T(state.time)
    solver.control = ControlState(
        T(control.time),
        T(control.angle_degrees),
        T(control.angular_velocity_degrees),
    )
    solver.solid = solid_mask(geometry, scenario.domain, solver.control.angle_degrees)
    try
        _project!(solver, max(scenario.output_dt, T(1.0e-4)))
    catch failure
        failure isa NumericalFailure || rethrow()
        return ImportOutcome(
            :rejected,
            failure.reason;
            warnings = [sprint(showerror, failure)],
        )
    end
    report = ImportReport(
        state.source_solver,
        solver_info(solver).id,
        ["pressure", "face-centered projection history"],
        ["Stable Fluids rebuilt pressure and face-projection history."],
    )
    return ImportOutcome(:accepted, :none; report, warnings = copy(report.warnings))
end

function diagnostics(solver::StableFluidsSolver)
    scenario, _ = _stable_require(solver)
    velocity = cell_velocity(solver)
    diagnostic_values = Dict{String,Float64}(
        "time" => Float64(solver.time),
        "requested_reynolds" => Float64(solver.reynolds_value),
        "kinetic_energy" => Float64(kinetic_energy(velocity)),
        "enstrophy" => Float64(enstrophy(velocity, scenario.domain)),
        "divergence_l2" => Float64(divergence_l2(velocity, scenario.domain)),
        "solid_leakage" => Float64(solid_leakage(velocity, solver.solid)),
        "wake_width" => Float64(wake_width(velocity, scenario.domain, scenario.foil.pivot[1])),
        "recirculation_area" => Float64(
            recirculation_area(velocity, scenario.domain, scenario.foil.pivot[1]),
        ),
        "projection_iterations" => Float64(solver.projection_iterations),
        "diffusion_iterations" => Float64(solver.diffusion_iterations),
    )
    all(isfinite, values(diagnostic_values)) ||
        throw(NumericalFailure(
            :nonfinite_state,
            "Stable Fluids produced non-finite diagnostics",
        ))
    return Diagnostics(diagnostic_values, String[])
end
