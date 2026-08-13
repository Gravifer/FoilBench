const STABLE_FLUIDS_INFO = SolverInfo(
    "stable-fluids",
    "Stable Fluids (MAC)",
    (2,),
    true,
    (:float32, :float64),
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
    revision::Int
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
        0,
    )
end

solver_info(::StableFluidsSolver) = STABLE_FLUIDS_INFO
reynolds(solver::StableFluidsSolver) = solver.reynolds_value
state_revision(solver::StableFluidsSolver) = solver.revision

function stable_transport_mode(solver::StableFluidsSolver)
    solver.skew_rk2 && return "skew-rk2"
    return solver.maccormack ? "maccormack" : "semi-lagrangian"
end

function set_stable_transport_mode!(solver::StableFluidsSolver, mode::AbstractString)
    mode in ("maccormack", "semi-lagrangian", "skew-rk2") ||
        throw(ArgumentError("unsupported Stable Fluids advection: $mode"))
    changed = stable_transport_mode(solver) != mode
    solver.maccormack = mode == "maccormack"
    solver.skew_rk2 = mode == "skew-rk2"
    changed && solver.scenario !== nothing && (solver.revision += 1)
    return stable_transport_mode(solver)
end

function interactive_tuning(solver::StableFluidsSolver)
    mode = stable_transport_mode(solver)
    return InteractiveTuning(
        "stable-advection", "adv", mode, mode,
        mode != "maccormack", mode != "skew-rk2",
    )
end

function adjust_interactive_tuning!(solver::StableFluidsSolver, direction::Integer)
    set_stable_transport_mode!(solver, direction < 0 ? "maccormack" : "skew-rk2")
    return interactive_tuning(solver)
end

function apply_interactive_tuning!(solver::StableFluidsSolver, value::InteractiveTuningValue)
    value isa String || throw(ArgumentError("Stable Fluids tuning value must be a string"))
    set_stable_transport_mode!(solver, value)
    return interactive_tuning(solver)
end

function set_reynolds!(solver::StableFluidsSolver{T}, selected::Real) where {T}
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
    solver.revision = 0
    _project!(solver, max(scenario.output_dt, T(1.0e-4)))
    solver.revision = 0
    return nothing
end

function restart!(
    solver::StableFluidsSolver{T},
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
    velocity = _initial_velocity(scenario)
    wall = wall_velocity_grid(geometry, scenario.domain, solver.control)
    for index in CartesianIndices(solver.solid)
        solver.solid[index] || continue
        velocity[index, 1] = wall[index, 1]
        velocity[index, 2] = wall[index, 2]
    end
    solver.u, solver.v = cell_to_faces(velocity)
    _project!(solver, max(scenario.output_dt, T(1.0e-4)))
    solver.revision = 0
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
        :projection,
        Dict{String,Any}(
            "projection_cfl" => projection_cfl,
            "maximum_projection_cfl" => projection_limit,
        ),
    ))
    channel_walls = option(scenario, "initial_condition", "") == "poiseuille"
    tolerance = option(scenario, "pressure_tolerance", T(1.0e-5))
    max_iterations = option(scenario, "pressure_max_iterations", 640)
    iterations, relative_residual, converged = project_faces!(
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
        :projection,
        Dict{String,Any}(
            "iterations" => iterations,
            "tolerance" => tolerance,
            "relative_residual" => relative_residual,
        ),
    ))
    all(isfinite, solver.u) && all(isfinite, solver.v) ||
        throw(NumericalFailure(
            :nonfinite_state,
            "Stable Fluids projection produced non-finite velocity",
        ))
    return relative_residual
end

cell_velocity(solver::StableFluidsSolver) = faces_to_cell(solver.u, solver.v)

function _diffuse_faces!(
    solver::StableFluidsSolver{T},
    viscosity::T,
    timestep::T,
) where {T}
    scenario, _ = _stable_require(solver)
    tolerance = option(scenario, "pressure_tolerance", T(1.0e-5))
    max_iterations = option(scenario, "pressure_max_iterations", 640)
    solver.u, u_iterations, u_residual, u_converged = implicit_diffuse_scalar(
        solver.u,
        viscosity,
        timestep,
        scenario.domain;
        tolerance,
        max_iterations,
    )
    solver.v, v_iterations, v_residual, v_converged = implicit_diffuse_scalar(
        solver.v,
        viscosity,
        timestep,
        scenario.domain;
        tolerance,
        max_iterations,
    )
    solver.diffusion_iterations = max(u_iterations, v_iterations)
    u_converged && v_converged ||
        throw(NumericalFailure(
            :convergence_failure,
            "Stable Fluids implicit viscosity did not converge",
            :viscosity,
            Dict{String,Any}(
                "iterations" => solver.diffusion_iterations,
                "tolerance" => tolerance,
                "final_residual" => max(u_residual, v_residual),
            ),
        ))
    return max(u_residual, v_residual)
end

function advance!(
    solver::StableFluidsSolver{T},
    control::ControlState,
    target_dt::Real,
) where {T}
    scenario, geometry = _stable_require(solver)
    target = validate_advance_request(solver.time, control, target_dt)
    maximum_speed = max(_maximum_speed(cell_velocity(solver)), abs(scenario.freestream[1]), eps(T))
    cfl = option(scenario, "stable_cfl", T(0.7))
    solver.skew_rk2 && (cfl = min(cfl, T(0.4)))
    spacing = min(dx(scenario.domain), dy(scenario.domain))
    radius = maximum_radius(geometry)
    wall_speed = abs(deg2rad(T(control.angular_velocity_degrees))) * radius
    sweep_cells = abs(deg2rad(T(control.angle_degrees) - solver.control.angle_degrees)) *
        radius / spacing
    advective_rate = solver.skew_rk2 ?
        skew_face_advection_rate(solver.u, solver.v, scenario.domain) :
        maximum_speed / spacing
    fluid_measure = target * advective_rate
    required = max(
        T(1.05) * fluid_measure / cfl,
        target * wall_speed / (cfl * spacing),
        sweep_cells / cfl,
    )
    substeps = max(1, ceil(Int, required))
    substeps <= 512 || throw(NumericalFailure(
        :stability_limit,
        "Stable Fluids motion requires too many internal substeps",
        solver.skew_rk2 ? :advection : :boundary,
        Dict{String,Any}(
            "required_substeps" => substeps,
            "maximum_substeps" => 512,
            "maximum_fluid_speed" => maximum_speed,
            "maximum_wall_speed" => wall_speed,
            "boundary_sweep_cells" => sweep_cells,
        ),
    ))
    timestep = target / T(substeps)
    viscosity = reference_speed(scenario) * scenario.foil.chord / solver.reynolds_value
    checkpoint = (
        copy(solver.u), copy(solver.v), copy(solver.solid), solver.control, solver.time,
        solver.projection_iterations, solver.diffusion_iterations, solver.revision,
    )
    start_time = solver.time
    start_angle = solver.control.angle_degrees
    pressure_residual = zero(T)
    diffusion_residual = zero(T)
    final_speed = maximum_speed
    accepted_measure = fluid_measure / T(substeps)
    stability_retries = 0
    while true
        try
            for substep in 1:substeps
                fraction = T(substep) / T(substeps)
                sub_control = ControlState(
                    start_time + fraction * target,
                    start_angle + fraction * (T(control.angle_degrees) - start_angle),
                    T(control.angular_velocity_degrees),
                )
                wall = wall_velocity_grid(geometry, scenario.domain, sub_control)
                if solver.skew_rk2
                    solver.u, solver.v = advect_faces_skew_rk2(
                        solver.u, solver.v, timestep, scenario.domain, solver.solid, wall,
                        scenario.freestream,
                    )
                    diffusion_residual = _diffuse_faces!(solver, viscosity, timestep)
                elseif solver.face_advection
                    solver.u, solver.v = advect_faces(
                        solver.u, solver.v, timestep, scenario.domain;
                        maccormack = solver.maccormack,
                    )
                    diffusion_residual = _diffuse_faces!(solver, viscosity, timestep)
                else
                    velocity = advect_velocity(
                        cell_velocity(solver), timestep, scenario.domain;
                        maccormack = solver.maccormack,
                    )
                    velocity, diffusion_iterations, diffusion_residual,
                        diffusion_converged = implicit_diffuse_velocity(
                        velocity, viscosity, timestep, scenario.domain;
                        tolerance = option(scenario, "pressure_tolerance", T(1.0e-5)),
                        max_iterations = option(scenario, "pressure_max_iterations", 640),
                    )
                    diffusion_converged || throw(NumericalFailure(
                        :convergence_failure,
                        "Stable Fluids implicit viscosity did not converge",
                        :viscosity,
                        Dict{String,Any}(
                            "iterations" => diffusion_iterations,
                            "tolerance" =>
                                option(scenario, "pressure_tolerance", T(1.0e-5)),
                            "final_residual" => diffusion_residual,
                        ),
                    ))
                    solver.diffusion_iterations = diffusion_iterations
                    solver.u, solver.v = cell_to_faces(velocity)
                end
                solver.control = sub_control
                solver.solid = solid_mask(
                    geometry, scenario.domain, sub_control.angle_degrees,
                )
                pressure_residual = _project!(solver, timestep)
            end
            final_speed = _maximum_speed(cell_velocity(solver))
            advective_rate = solver.skew_rk2 ?
                skew_face_advection_rate(solver.u, solver.v, scenario.domain) :
                final_speed / spacing
            accepted_measure = timestep * advective_rate
        catch error
            solver.u = copy(checkpoint[1])
            solver.v = copy(checkpoint[2])
            solver.solid = copy(checkpoint[3])
            solver.control, solver.time, solver.projection_iterations,
                solver.diffusion_iterations, solver.revision = checkpoint[4:end]
            if error isa NumericalFailure && error.reason == :excessive_velocity &&
                    error.stage == :projection
                observed = get(error.evidence, "projection_cfl", NaN)
                limit = get(error.evidence, "maximum_projection_cfl", NaN)
                isfinite(observed) && isfinite(limit) && observed > limit > 0 || rethrow()
                next_substeps = max(
                    substeps + 1,
                    ceil(Int, T(1.05) * T(substeps) * T(observed) / T(limit)),
                )
                next_substeps <= 512 || rethrow()
                stability_retries += 1
                substeps = next_substeps
                timestep = target / T(substeps)
                continue
            end
            rethrow()
        end
        accepted_measure <= cfl * (one(T) + T(1.0e-6)) && break
        next_substeps = max(
            substeps + 1,
            ceil(Int, T(1.05) * T(substeps) * accepted_measure / cfl),
        )
        solver.u = copy(checkpoint[1])
        solver.v = copy(checkpoint[2])
        solver.solid = copy(checkpoint[3])
        solver.control, solver.time, solver.projection_iterations,
            solver.diffusion_iterations, solver.revision = checkpoint[4:end]
        next_substeps <= 512 || throw(NumericalFailure(
            :stability_limit,
            "Stable Fluids retry requires too many internal substeps",
            :advection,
            Dict{String,Any}(
                "accepted_measure" => accepted_measure,
                "maximum_measure" => cfl,
                "required_substeps" => next_substeps,
                "maximum_substeps" => 512,
                "stability_retries" => stability_retries,
            ),
        ))
        stability_retries += 1
        substeps = next_substeps
        timestep = target / T(substeps)
    end
    final_control = ControlState(
        T(control.time), T(control.angle_degrees), T(control.angular_velocity_degrees),
    )
    final_wall = wall_velocity_grid(geometry, scenario.domain, final_control)
    native_divergence = native_divergence_linf(
        solver.u, solver.v, scenario.domain, solver.solid,
    )
    native_leakage = solid_face_leakage(
        solver.u, solver.v, solver.solid, final_wall,
    )
    divergence_limit = mac_postcondition_limit(
        scenario, "mac_maximum_divergence_linf",
    )
    leakage_limit = mac_postcondition_limit(
        scenario, "mac_maximum_solid_leakage",
    )
    if native_divergence > divergence_limit || native_leakage > leakage_limit
        solver.u = copy(checkpoint[1])
        solver.v = copy(checkpoint[2])
        solver.solid = copy(checkpoint[3])
        solver.control, solver.time, solver.projection_iterations,
            solver.diffusion_iterations, solver.revision = checkpoint[4:end]
        throw(NumericalFailure(
            :postcondition_failure,
            "Stable Fluids exceeded a configured MAC postcondition limit",
            :postcondition,
            Dict{String,Any}(
                "divergence_linf" => native_divergence,
                "maximum_divergence_linf" => divergence_limit,
                "solid_leakage" => native_leakage,
                "maximum_solid_leakage" => leakage_limit,
            ),
        ))
    end
    solver.time = T(control.time)
    solver.control = ControlState(
        solver.time,
        T(control.angle_degrees),
        T(control.angular_velocity_degrees),
    )
    solver.revision += 1
    return StepReport(
        target, target, substeps, final_speed, String[], solver.revision,
        Dict{String,Any}(
            "maximum_fluid_speed" => final_speed,
            "maximum_wall_speed" => wall_speed,
            "maximum_characteristic_displacement" =>
                accepted_measure,
            "maximum_advective_rate" => advective_rate,
            "maximum_boundary_sweep" => sweep_cells / substeps,
            "stability_retries" => stability_retries,
            "pressure_converged" => true,
            "pressure_iterations" => solver.projection_iterations,
            "pressure_relative_residual" => pressure_residual,
            "viscosity_converged" => true,
            "viscosity_iterations" => solver.diffusion_iterations,
            "viscosity_final_residual" => diffusion_residual,
            "divergence_linf" => native_divergence,
            "solid_leakage" => native_leakage,
            "requested_reynolds" => solver.reynolds_value,
            "effective_reynolds" => solver.reynolds_value,
            "degraded_motion" => wall_speed == zero(T) &&
                abs(T(control.angle_degrees) - start_angle) > T(1.0e-9),
            "projection_iterations" => solver.projection_iterations,
            "diffusion_iterations" => solver.diffusion_iterations,
        ),
    )
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
    velocity = cell_velocity(solver)
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
    solver::StableFluidsSolver{T},
    state::CanonicalFlowState{2,S},
    control::ControlState,
) where {T,S}
    scenario, geometry = _stable_require(solver)
    checkpoint = (
        copy(solver.u), copy(solver.v), copy(solver.solid), solver.control, solver.time,
        solver.projection_iterations, solver.diffusion_iterations, solver.revision,
    )
    try
        validate_canonical_import(state, scenario, control)
        imported = T.(canonical_to_cell(state))
        solver.time = T(state.time)
        solver.control = ControlState(
            T(control.time), T(control.angle_degrees), T(control.angular_velocity_degrees),
        )
        solver.solid = solid_mask(geometry, scenario.domain, solver.control.angle_degrees)
        wall = wall_velocity_grid(geometry, scenario.domain, solver.control)
        for index in CartesianIndices(solver.solid)
            solver.solid[index] || continue
            imported[index, 1] = wall[index, 1]
            imported[index, 2] = wall[index, 2]
        end
        solver.u, solver.v = cell_to_faces(imported)
        # Canonical reconstruction does not advance physical time.  A small
        # projection pseudo-step keeps import admissibility independent of the
        # scenario output interval while retaining the excessive-speed guard.
        _project!(solver, T(1.0e-4))
    catch failure
        solver.u, solver.v, solver.solid, solver.control, solver.time,
            solver.projection_iterations, solver.diffusion_iterations, solver.revision = checkpoint
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
        ["pressure", "face-centered projection history"],
        ["Stable Fluids rebuilt pressure and face-projection history."],
    )
    return ImportOutcome(:accepted, :none; report, warnings = copy(report.warnings))
end

function diagnostics(solver::StableFluidsSolver)
    scenario, geometry = _stable_require(solver)
    velocity = cell_velocity(solver)
    wall = wall_velocity_grid(geometry, scenario.domain, solver.control)
    diagnostic_values = Dict{String,Float64}(
        "time" => Float64(solver.time),
        "requested_reynolds" => Float64(solver.reynolds_value),
        "kinetic_energy" => Float64(kinetic_energy(velocity)),
        "enstrophy" => Float64(enstrophy(velocity, scenario.domain)),
        "divergence_l2" => Float64(divergence_l2(velocity, scenario.domain)),
        "divergence_linf" => Float64(native_divergence_linf(
            solver.u, solver.v, scenario.domain, solver.solid,
        )),
        "solid_leakage" => Float64(solid_face_leakage(
            solver.u, solver.v, solver.solid, wall,
        )),
        "wake_width" => Float64(wake_width(
            velocity,
            scenario.domain,
            scenario.foil.pivot[1];
            chord = scenario.foil.chord,
            freestream_u = scenario.freestream[1],
            solid = solver.solid,
        )),
        "recirculation_area" => Float64(
            recirculation_area(
                velocity,
                scenario.domain,
                scenario.foil.pivot[1];
                solid = solver.solid,
            ),
        ),
        "projection_iterations" => Float64(solver.projection_iterations),
        "diffusion_iterations" => Float64(solver.diffusion_iterations),
    )
    all(isfinite, values(diagnostic_values)) ||
        throw(NumericalFailure(
            :nonfinite_state,
            "Stable Fluids produced non-finite diagnostics",
        ))
    return Diagnostics(diagnostic_values, String[], solver.revision)
end
