#!/usr/bin/env julia

using FoilBenchJulia
using JSON3

function calibrated_scenario(base::Scenario{2,T}, resolution::NTuple{2,Int}) where {T}
    domain = DomainSpec(base.domain.bounds, resolution, base.domain.periodic_axes)
    controls = [ControlKeyframe(zero(T), zero(T)), ControlKeyframe(base.duration, zero(T))]
    return Scenario(
        base.schema_version,
        base.id,
        domain,
        base.reynolds,
        base.freestream,
        base.foil,
        controls,
        base.duration,
        base.output_dt,
        base.precision,
        base.seed,
        copy(base.solver_options),
    )
end

function create_solver(solver_id::AbstractString, ::Type{T}) where {T<:AbstractFloat}
    solver_id == "stable-fluids" && return StableFluidsSolver(T)
    solver_id == "lbm-d2q9" && return LBMSolver(T)
    solver_id == "pic-flip" && return PicFlipSolver(T)
    throw(ArgumentError("unknown solver: $solver_id"))
end

function failure_reason(error)::String
    error isa NumericalFailure && return String(error.reason)
    return String(nameof(typeof(error)))
end

function run_drag_calibration(
    base::Scenario{2,T},
    resolution::NTuple{2,Int},
    solver_id::AbstractString,
    candidate::Dict{String,Any},
    trace::Dict{String,Any},
) where {T}
    scenario = calibrated_scenario(base, resolution)
    solver = create_solver(solver_id, T)
    initialize!(solver, scenario, NacaFoil(scenario.foil), scenario.seed)
    cap = Float64(candidate["tip_speed_cap"])
    window = Float64(candidate["smoothing_window_seconds"])
    samples = trace["samples"]
    recent = Tuple{Float64,Float64}[]
    maximum_measured = 0.0
    maximum_solver = 0.0
    maximum_flow = 0.0
    successful = 0
    failure = nothing
    physical_time = zero(T)
    speed = Float64(reference_speed(scenario))
    chord = Float64(scenario.foil.chord)
    started = time_ns()
    extended = vcat(samples, Any[[Float64(samples[end][1]) + 0.02, samples[end][2]]])
    for (sample_index, sample) in enumerate(extended)
        timestamp = Float64(sample[1])
        angle = clamp(Float64(sample[2]), -30.0, 30.0)
        measured_degrees = 0.0
        if sample_index <= length(samples)
            push!(recent, (timestamp, angle))
            cutoff = timestamp - window
            while length(recent) > 2 && recent[2][1] < cutoff
                popfirst!(recent)
            end
            if length(recent) >= 2 && timestamp > recent[1][1]
                measured_degrees = (angle - recent[1][2]) / (timestamp - recent[1][1])
            end
        end
        measured_ratio = abs(deg2rad(measured_degrees)) * chord / speed
        solver_ratio = min(measured_ratio, cap)
        maximum_measured = max(maximum_measured, measured_ratio)
        maximum_solver = max(maximum_solver, solver_ratio)
        omega = iszero(measured_degrees) ? 0.0 :
            copysign(rad2deg(solver_ratio * speed / chord), measured_degrees)
        physical_time += scenario.output_dt
        try
            report = advance!(
                solver,
                ControlState(physical_time, T(angle), T(omega)),
                scenario.output_dt,
            )
            maximum_flow = max(maximum_flow, Float64(report.max_speed))
            successful += 1
        catch error
            failure = failure_reason(error)
            break
        end
    end
    return Dict{String,Any}(
        "candidate" => candidate["id"],
        "solver" => solver_id,
        "trace" => trace["id"],
        "tip_speed_cap" => cap,
        "smoothing_window_seconds" => window,
        "max_measured_tip_speed_ratio" => maximum_measured,
        "max_solver_tip_speed_ratio" => maximum_solver,
        "successful_steps" => successful,
        "requested_steps" => length(samples) + 1,
        "failure_reason" => failure,
        "maximum_flow_speed" => maximum_flow,
        "wall_seconds" => (time_ns() - started) / 1.0e9,
    )
end

root = find_repository_root(@__DIR__)
fixture = JSON3.read(
    read(joinpath(root, "spec", "conformance", "drag-calibration.json"), String),
    Dict{String,Any},
)
resolution = (Int(fixture["resolution"][1]), Int(fixture["resolution"][2]))
base = load_scenario(joinpath(root, fixture["scenario"]))
runs = [
    run_drag_calibration(base, resolution, solver_id, candidate, trace)
    for candidate in fixture["candidates"]
    for solver_id in fixture["solvers"]
    for trace in fixture["traces"]
]
result = Dict{String,Any}(
    "schema_version" => 1,
    "contract_id" => "foilbench-phase2-v1-drag-calibration",
    "language" => "julia",
    "scenario" => fixture["scenario"],
    "resolution" => collect(resolution),
    "runs" => runs,
)
text = sprint(io -> JSON3.pretty(io, result))
if !isempty(ARGS)
    output_path = abspath(ARGS[1])
    mkpath(dirname(output_path))
    write(output_path, text)
end
println(text)
