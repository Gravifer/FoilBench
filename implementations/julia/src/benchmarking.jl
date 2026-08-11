struct BenchmarkMatrix
    id::String
    scenario_path::String
    solvers::Vector{String}
    resolutions::Vector{NTuple{2,Int}}
    duration::Float64
    repetitions::Int
    save_snapshots::Bool
end

function find_repository_root(path::AbstractString)
    selected = isdir(path) ? abspath(path) : dirname(abspath(path))
    while true
        isfile(joinpath(selected, "spec", "scenario.schema.json")) &&
            isdir(joinpath(selected, "implementations")) && return selected
        parent = dirname(selected)
        parent == selected && throw(ArgumentError("could not locate FoilBench repository root"))
        selected = parent
    end
end

function load_benchmark_matrix(path::AbstractString)
    document = JSON3.read(read(path, String), Dict{String,Any})
    root = find_repository_root(path)
    validate_json_file(document, joinpath(root, "spec", "benchmark-matrix.schema.json"))
    resolutions = NTuple{2,Int}[
        (Int(value[1]), Int(value[2])) for value in document["resolutions"]
    ]
    all(resolution -> all(>=(4), resolution), resolutions) ||
        throw(ArgumentError("benchmark resolutions must be at least four"))
    repetitions = Int(document["repetitions"])
    repetitions >= 1 || throw(ArgumentError("benchmark repetitions must be positive"))
    duration = Float64(document["duration"])
    duration > 0 || throw(ArgumentError("benchmark duration must be positive"))
    return BenchmarkMatrix(
        String(document["id"]),
        joinpath(root, String(document["scenario"])),
        String.(document["solvers"]),
        resolutions,
        duration,
        repetitions,
        Bool(document["save_snapshots"]),
    )
end

function scenario_with_run(
    scenario::Scenario{2,T},
    resolution::NTuple{2,Int},
    duration::Real,
) where {T}
    domain = DomainSpec(scenario.domain.bounds, resolution, scenario.domain.periodic_axes)
    return Scenario(
        scenario.schema_version,
        scenario.id,
        domain,
        scenario.reynolds,
        scenario.freestream,
        scenario.foil,
        scenario.controls,
        T(duration),
        scenario.output_dt,
        scenario.precision,
        scenario.seed,
        copy(scenario.solver_options),
    )
end

function create_solver(solver_id::AbstractString, ::Type{T} = Float32) where {T<:AbstractFloat}
    solver_id == "stable-fluids" && return StableFluidsSolver(T)
    solver_id == "lbm-d2q9" && return LBMSolver(T)
    solver_id == "pic-flip" && return PicFlipSolver(T)
    throw(ArgumentError("unknown Julia solver: $solver_id"))
end

solver_ids() = ("stable-fluids", "lbm-d2q9", "pic-flip")

function describe_implementation()
    entries = Dict{String,Any}[]
    for solver_id in solver_ids()
        info = solver_info(create_solver(solver_id))
        push!(entries, Dict{String,Any}(
            "id" => info.id,
            "display_name" => info.display_name,
            "dimensions" => collect(info.dimensions),
            "moving_boundary" => info.supports_moving_boundary,
            "acceleration" => String(info.acceleration),
        ))
    end
    return Dict{String,Any}(
        "implementation" => "julia",
        "version" => "0.1.0",
        "canonical_reference" => false,
        "thin_3d" => false,
        "solvers" => entries,
    )
end

function validate_benchmark_result(result::Dict{String,Any}, schema_path::AbstractString)
    return validate_json_file(result, schema_path)
end

function _percentile(values::Vector{Float64}, fraction::Float64)
    isempty(values) && return 0.0
    sorted = sort(values)
    position = 1 + fraction * (length(sorted) - 1)
    lower = floor(Int, position)
    upper = ceil(Int, position)
    lower == upper && return sorted[lower]
    weight = position - lower
    return (1 - weight) * sorted[lower] + weight * sorted[upper]
end

function _git_commit(root::AbstractString)
    try
        return readchomp(Cmd(`git rev-parse HEAD`; dir = root))
    catch
        return "unknown"
    end
end

function _machine_description()
    return Dict{String,Any}(
        "platform" => string(Sys.KERNEL),
        "architecture" => string(Sys.ARCH),
        "julia" => string(VERSION),
        "logical_cpus" => Sys.CPU_THREADS,
        "threads" => Threads.nthreads(),
    )
end

function _write_json(path::AbstractString, value)
    open(path, "w") do io
        JSON3.pretty(io, value)
    end
    return nothing
end

function run_benchmark_matrix(
    matrix_path::AbstractString,
    output_root::Union{Nothing,AbstractString} = nothing,
)
    matrix = load_benchmark_matrix(matrix_path)
    root = find_repository_root(matrix_path)
    timestamp = Dates.format(Dates.now(), "yyyymmdd-HHMMSS")
    destination = output_root === nothing ? joinpath(root, "results", matrix.id, timestamp) :
        abspath(output_root)
    mkpath(destination)
    scenario_base = load_scenario(matrix.scenario_path)
    dimension(scenario_base) == 2 || throw(ArgumentError("Phase 2A benchmarks support only 2D"))
    schema_path = joinpath(root, "spec", "result.schema.json")
    rows = Vector{Vector{String}}()
    for resolution in matrix.resolutions
        scenario = scenario_with_run(scenario_base, resolution, matrix.duration)
        T = scalar_type(scenario)
        geometry = NacaFoil(scenario.foil)
        for solver_id in matrix.solvers, repetition in 1:matrix.repetitions
            initialization_started = time_ns()
            cold_solver = create_solver(solver_id, T)
            initialize!(cold_solver, scenario, geometry, scenario.seed)
            initialization_seconds = (time_ns() - initialization_started) / 1.0e9
            cold_dt = min(scenario.output_dt, scenario.duration)
            cold_started = time_ns()
            advance!(cold_solver, control_at(scenario, cold_dt), cold_dt)
            cold_step_seconds = (time_ns() - cold_started) / 1.0e9

            solver = create_solver(solver_id, T)
            initialize!(solver, scenario, geometry, scenario.seed)
            elapsed_simulated = zero(T)
            step_seconds = Float64[]
            total_substeps = 0
            warnings = String[]
            success = true
            diagnostic_values = Dict{String,Float64}()
            try
                while elapsed_simulated < scenario.duration - T(1.0e-12)
                    timestep = min(scenario.output_dt, scenario.duration - elapsed_simulated)
                    control = control_at(scenario, elapsed_simulated + timestep)
                    started = time_ns()
                    report = advance!(solver, control, timestep)
                    push!(step_seconds, (time_ns() - started) / 1.0e9)
                    elapsed_simulated += report.advanced_dt
                    total_substeps += report.substeps
                    append!(warnings, report.warnings)
                end
                selected_diagnostics = diagnostics(solver)
                diagnostic_values = copy(selected_diagnostics.values)
                append!(warnings, selected_diagnostics.warnings)
            catch error
                success = false
                push!(warnings, "$(typeof(error)): $(sprint(showerror, error))")
            end
            total_wall = sum(step_seconds)
            median = _percentile(step_seconds, 0.5)
            p95 = _percentile(step_seconds, 0.95)
            particle_count = get(diagnostic_values, "particle_count", 0.0)
            result = Dict{String,Any}(
                "schema_version" => 1,
                "benchmark_matrix_id" => matrix.id,
                "scenario_id" => scenario.id,
                "language" => "julia",
                "solver" => solver_id,
                "git_commit" => _git_commit(root),
                "machine" => _machine_description(),
                "precision" => T === Float32 ? "float32" : "float64",
                "resolution" => collect(resolution),
                "bounds" => [collect(pair) for pair in scenario.domain.bounds],
                "periodic_axes" => [string(axis) for axis in scenario.domain.periodic_axes],
                "reynolds" => Float64(scenario.reynolds),
                "freestream" => collect(scenario.freestream),
                "foil" => Dict{String,Any}(
                    "naca" => scenario.foil.naca,
                    "chord" => Float64(scenario.foil.chord),
                    "pivot" => collect(scenario.foil.pivot),
                ),
                "control_history" => [Dict{String,Any}(
                    "time" => Float64(keyframe.time),
                    "angle_degrees" => Float64(keyframe.angle_degrees),
                ) for keyframe in scenario.controls],
                "requested_duration" => matrix.duration,
                "simulated_duration" => Float64(elapsed_simulated),
                "output_dt" => Float64(scenario.output_dt),
                "seed" => Int(scenario.seed),
                "initialization_seconds" => initialization_seconds,
                "cold_step_seconds" => cold_step_seconds,
                "step_seconds" => step_seconds,
                "median_step_seconds" => median,
                "p95_step_seconds" => p95,
                "simulated_seconds_per_wall_second" => total_wall > 0 ? Float64(elapsed_simulated) / total_wall : 0.0,
                "cell_updates_per_second" => total_wall > 0 ? prod(resolution) * total_substeps / total_wall : 0.0,
                "particle_updates_per_second" => total_wall > 0 ? particle_count * total_substeps / total_wall : 0.0,
                "peak_rss_bytes" => max(Int(Sys.maxrss()), 0),
                "substeps" => total_substeps,
                "diagnostics" => diagnostic_values,
                "success" => success,
                "warnings" => sort!(unique(warnings)),
            )
            validate_benchmark_result(result, schema_path)
            stem = "$solver_id-$(resolution[1])x$(resolution[2])-r$repetition"
            _write_json(joinpath(destination, "$stem.json"), result)
            matrix.save_snapshots && success &&
                save_canonical_state(export_state(solver), joinpath(destination, "$stem-state"))
            push!(rows, [
                solver_id,
                "$(resolution[1])x$(resolution[2])",
                string(repetition),
                string(initialization_seconds),
                string(cold_step_seconds),
                string(median),
                string(p95),
                string(result["simulated_seconds_per_wall_second"]),
                string(result["cell_updates_per_second"]),
                string(result["particle_updates_per_second"]),
                string(result["peak_rss_bytes"]),
                string(success),
            ])
        end
    end
    header = ["solver", "resolution", "repetition", "initialization_seconds",
        "cold_step_seconds", "median_step_seconds", "p95_step_seconds",
        "simulated_seconds_per_wall_second", "cell_updates_per_second",
        "particle_updates_per_second", "peak_rss_bytes", "success"]
    open(joinpath(destination, "summary.csv"), "w") do io
        println(io, join(header, ','))
        for row in rows
            println(io, join(row, ','))
        end
    end
    return destination
end

function collect_benchmark_results(directory::AbstractString)
    results = Dict{String,Any}[]
    for (root, _, files) in walkdir(directory), file in sort(files)
        endswith(file, ".json") || continue
        value = try
            JSON3.read(read(joinpath(root, file), String), Dict{String,Any})
        catch
            continue
        end
        get(value, "schema_version", 0) == 1 && haskey(value, "solver") && push!(results, value)
    end
    return results
end

function format_benchmark_comparison(directory::AbstractString)
    results = collect_benchmark_results(directory)
    isempty(results) && return "No benchmark result JSON files found."
    header = rpad("language", 12) * rpad("solver", 20) * lpad("median ms", 12) *
        lpad("p95 ms", 12) * lpad("sim/wall", 12) * lpad("success", 10)
    lines = [header, repeat('-', length(header))]
    for result in results
        push!(lines,
            rpad(string(result["language"]), 12) * rpad(string(result["solver"]), 20) *
            lpad(string(round(1000 * Float64(result["median_step_seconds"]); digits = 3)), 12) *
            lpad(string(round(1000 * Float64(result["p95_step_seconds"]); digits = 3)), 12) *
            lpad(string(round(Float64(result["simulated_seconds_per_wall_second"]); digits = 3)), 12) *
            lpad(string(result["success"]), 10),
        )
    end
    return join(lines, '\n')
end
