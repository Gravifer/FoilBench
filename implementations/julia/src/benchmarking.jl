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
    validate_json_file(result, schema_path)
    return validate_benchmark_result_semantics(result)
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

function benchmark_recovery_window(scenario::Scenario, duration::Real = scenario.duration)
    initial_angle = first(scenario.controls).angle_degrees
    last_angle = last(scenario.controls).angle_degrees
    isapprox(initial_angle, last_angle; atol = 1.0e-9, rtol = 0) || return nothing
    changed = findall(
        control -> !isapprox(
            control.angle_degrees,
            initial_angle;
            atol = 1.0e-9,
            rtol = 0,
        ),
        scenario.controls,
    )
    isempty(changed) && return nothing
    first_changed = first(changed)
    last_changed = last(changed)
    (first_changed == 1 || last_changed + 1 > length(scenario.controls)) && return nothing
    baseline_end = scenario.controls[first_changed - 1].time
    recovery_start = scenario.controls[last_changed + 1].time
    (baseline_end >= recovery_start || recovery_start >= duration) && return nothing
    return (Float64(baseline_end), Float64(recovery_start))
end

function analyze_benchmark_wake(
    samples::Vector{Float64},
    sample_dt::Real,
    chord::Real,
    freestream_speed::Real,
)
    length(samples) >= 8 || throw(ArgumentError("wake spectrum requires eight samples"))
    sample_dt > 0 && chord > 0 && freestream_speed > 0 ||
        throw(ArgumentError("wake spectrum scales must be positive"))
    all(isfinite, samples) || throw(ArgumentError("wake probe samples must be finite"))
    count = length(samples)
    centered = samples .- sum(samples) / count
    transverse_rms = sqrt(sum(abs2, centered) / count)
    windowed = [
        centered[index] * 0.5 * (1 - cos(2pi * (index - 1) / (count - 1)))
        for index in eachindex(centered)
    ]
    total_power = 0.0
    dominant_power = 0.0
    dominant_index = 0
    for frequency_index in 1:fld(count, 2)
        real_part = 0.0
        imaginary_part = 0.0
        for sample_index in 0:(count - 1)
            phase = 2pi * frequency_index * sample_index / count
            value = windowed[sample_index + 1]
            real_part += value * cos(phase)
            imaginary_part -= value * sin(phase)
        end
        power = real_part^2 + imaginary_part^2
        total_power += power
        if power > dominant_power
            dominant_power = power
            dominant_index = frequency_index
        end
    end
    frequency_resolution = inv(count * Float64(sample_dt))
    dominant_frequency = total_power <= floatmin(Float64) ?
        0.0 : dominant_index * frequency_resolution
    return Dict{String,Float64}(
        "wake_probe_samples" => count,
        "wake_frequency_resolution" => frequency_resolution,
        "wake_transverse_rms" => transverse_rms,
        "wake_mixing_index" => transverse_rms / Float64(freestream_speed),
        "wake_dominant_frequency" => dominant_frequency,
        "wake_strouhal_number" => dominant_frequency * Float64(chord) /
            Float64(freestream_speed),
        "wake_dominant_power_fraction" => total_power <= floatmin(Float64) ?
            0.0 : dominant_power / total_power,
    )
end

function _benchmark_evidence(evidence::Dict{String,Any})
    output = Dict{String,Any}()
    for (key, value) in evidence
        (value === nothing || value isa Bool || value isa Real || value isa AbstractString) ||
            throw(ArgumentError("benchmark evidence $key is not a JSON scalar"))
        value isa Real && !isfinite(value) &&
            throw(ArgumentError("benchmark evidence $key must be finite"))
        output[key] = value
    end
    return output
end

function _benchmark_solver_configuration(scenario::Scenario)
    return Dict{String,Any}(
        "initial_condition" => option(scenario, "initial_condition", "freestream"),
        "stable_advection" => option(scenario, "stable_advection", "maccormack"),
        "stable_face_advection" => option(scenario, "stable_face_advection", false),
        "stable_cfl" => Float64(option(scenario, "stable_cfl", 0.7)),
        "pressure_tolerance" => Float64(option(scenario, "pressure_tolerance", 1.0e-5)),
        "pressure_max_iterations" => option(scenario, "pressure_max_iterations", 640),
        "pic_flip_blend" => Float64(option(scenario, "pic_flip_blend", 0.95)),
        "pic_population_interval" => option(scenario, "pic_population_interval", 8),
        "pic_cfl" => Float64(option(scenario, "pic_cfl", 0.75)),
    )
end

function _require_finite_artifact(value, path::AbstractString = "result")
    value isa Real && !(value isa Bool) && !isfinite(value) &&
        throw(ArgumentError("$path contains a non-finite number"))
    if value isa AbstractDict
        for (name, child) in value
            _require_finite_artifact(child, "$path.$name")
        end
    elseif value isa AbstractVector
        for (index, child) in pairs(value)
            _require_finite_artifact(child, "$path[$index]")
        end
    end
    return nothing
end

function validate_benchmark_result_semantics(result::AbstractDict)
    _require_finite_artifact(result)
    success = result["success"] === true
    last_step = result["last_step"]
    if success
        result["failure"] === nothing ||
            throw(ArgumentError("successful benchmark result contains a failure"))
        last_step isa AbstractDict && !isempty(result["step_seconds"]) ||
            throw(ArgumentError("successful benchmark result lacks completed-step semantics"))
        final_revision = result["final_state_revision"]
        result["diagnostic_state_revision"] == final_revision &&
            last_step["state_revision"] == final_revision ||
            throw(ArgumentError("successful benchmark result contains stale revision evidence"))
        requested = Float64(result["requested_duration"])
        simulated = Float64(result["simulated_duration"])
        tolerance = (result["precision"] == "float32" ? 1.0e-6 : 1.0e-12) *
            max(1.0, abs(requested))
        abs(simulated - requested) <= tolerance ||
            throw(ArgumentError("successful benchmark result did not complete requested duration"))
    else
        result["failure"] isa AbstractDict ||
            throw(ArgumentError("failed benchmark result lacks structured failure evidence"))
    end
    step_seconds = Float64.(result["step_seconds"])
    if !isempty(step_seconds)
        total_wall = sum(step_seconds)
        substeps = Int(result["substeps"])
        cells = prod(Int.(result["resolution"]))
        particle_count = Float64(get(result["diagnostics"], "particle_count", 0.0))
        expected = Dict(
            "median_step_seconds" => _percentile(step_seconds, 0.5),
            "p95_step_seconds" => _percentile(step_seconds, 0.95),
            "simulated_seconds_per_wall_second" => Float64(result["simulated_duration"]) / total_wall,
            "cell_updates_per_second" => cells * substeps / total_wall,
            "particle_updates_per_second" => particle_count * substeps / total_wall,
        )
        for (field, expected_value) in expected
            isapprox(Float64(result[field]), expected_value; rtol = 1.0e-10, atol = 1.0e-12) ||
                throw(ArgumentError("benchmark result contains inconsistent derived field $field"))
        end
    end
    (result["memory_measurement"] == "unavailable") ==
        (result["peak_rss_bytes"] === nothing) ||
        throw(ArgumentError("memory measurement kind and RSS value disagree"))
    return nothing
end

function _benchmark_step(report::Union{Nothing,StepReport})
    report === nothing && return nothing
    return Dict{String,Any}(
        "requested_dt" => Float64(report.requested_dt),
        "advanced_dt" => Float64(report.advanced_dt),
        "substeps" => report.substeps,
        "max_speed" => Float64(report.max_speed),
        "state_revision" => report.state_revision,
        "evidence" => _benchmark_evidence(report.evidence),
        "warnings" => copy(report.warnings),
    )
end

function _benchmark_failure(error)
    if error isa NumericalFailure
        return Dict{String,Any}(
            "kind" => "numerical",
            "reason" => String(error.reason),
            "stage" => String(error.stage),
            "message" => sprint(showerror, error),
            "evidence" => _benchmark_evidence(error.evidence),
        )
    end
    return Dict{String,Any}(
        "kind" => "unexpected",
        "reason" => nothing,
        "stage" => nothing,
        "message" => "$(typeof(error)): $(sprint(showerror, error))",
        "evidence" => Dict{String,Any}(),
    )
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
            initialization_seconds = 0.0
            cold_step_seconds = 0.0
            solver = nothing
            elapsed_simulated = zero(T)
            step_seconds = Float64[]
            total_substeps = 0
            warnings = String[]
            success = true
            diagnostic_values = Dict{String,Float64}()
            diagnostic_revision = nothing
            last_report = nothing
            failure = nothing
            wake_probe = Float64[]
            recovery_times = benchmark_recovery_window(scenario, matrix.duration)
            recovery_baseline = nothing
            recovery_elapsed = nothing
            probe = reshape(T[
                min(
                    scenario.foil.pivot[1] + T(1.5) * scenario.foil.chord,
                    scenario.domain.bounds[1][2] - T(0.5) * dx(scenario.domain),
                ),
                scenario.foil.pivot[2],
            ], 2, 1)
            try
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
                while elapsed_simulated < scenario.duration - T(1.0e-12)
                    timestep = min(scenario.output_dt, scenario.duration - elapsed_simulated)
                    control = control_at(scenario, elapsed_simulated + timestep)
                    started = time_ns()
                    report = advance!(solver, control, timestep)
                    last_report = report
                    push!(step_seconds, (time_ns() - started) / 1.0e9)
                    elapsed_simulated += report.advanced_dt
                    total_substeps += report.substeps
                    append!(warnings, report.warnings)
                    elapsed_simulated >= T(0.5) * scenario.duration &&
                        push!(wake_probe, Float64(sample_velocity(solver, probe)[2, 1]))
                    if recovery_times !== nothing
                        baseline_end, recovery_start = recovery_times
                        crossed_baseline = recovery_baseline === nothing &&
                            elapsed_simulated >= baseline_end
                        observing_recovery = recovery_baseline !== nothing &&
                            recovery_elapsed === nothing &&
                            elapsed_simulated >= recovery_start
                        if crossed_baseline || observing_recovery
                            transient = diagnostics(solver).values
                            wake = get(transient, "wake_width", 0.0)
                            recirculation = get(transient, "recirculation_area", 0.0)
                            if crossed_baseline
                                recovery_baseline = (wake, recirculation)
                            else
                                baseline_wake, baseline_recirculation =
                                    something(recovery_baseline)
                                if wake <= max(1.25 * baseline_wake, 2 * dy(scenario.domain)) &&
                                        recirculation <= max(
                                            1.25 * baseline_recirculation,
                                            2 * dx(scenario.domain) * dy(scenario.domain),
                                        )
                                    recovery_elapsed = Float64(elapsed_simulated) - recovery_start
                                end
                            end
                        end
                    end
                end
                selected_diagnostics = diagnostics(solver)
                selected_diagnostics.state_revision == state_revision(solver) ||
                    error("benchmark diagnostics describe a stale state revision")
                diagnostic_revision = selected_diagnostics.state_revision
                diagnostic_values = copy(selected_diagnostics.values)
                append!(warnings, selected_diagnostics.warnings)
                if length(wake_probe) >= 8
                    merge!(
                        diagnostic_values,
                        analyze_benchmark_wake(
                            wake_probe,
                            scenario.output_dt,
                            scenario.foil.chord,
                            max(norm(scenario.freestream), T(1.0e-12)),
                        ),
                    )
                end
                if recovery_times !== nothing && recovery_baseline !== nothing
                    baseline_end, recovery_start = recovery_times
                    observed = recovery_elapsed !== nothing
                    diagnostic_values["recovery_baseline_time"] = baseline_end
                    diagnostic_values["recovery_start_time"] = recovery_start
                    diagnostic_values["recovery_observed"] = Float64(observed)
                    diagnostic_values["recovery_elapsed"] = something(
                        recovery_elapsed,
                        matrix.duration - recovery_start,
                    )
                    observed || push!(
                        warnings,
                        "wake recovery was not observed; recovery_elapsed is right-censored",
                    )
                end
            catch error
                success = false
                push!(warnings, "$(typeof(error)): $(sprint(showerror, error))")
                empty!(diagnostic_values)
                diagnostic_revision = nothing
                failure = _benchmark_failure(error)
            end
            total_wall = sum(step_seconds)
            median = _percentile(step_seconds, 0.5)
            p95 = _percentile(step_seconds, 0.95)
            particle_count = get(diagnostic_values, "particle_count", 0.0)
            result = Dict{String,Any}(
                "schema_version" => 1,
                "contract_id" => "foilbench-phase2-v1",
                "contract_revision" => 4,
                "benchmark_matrix_id" => matrix.id,
                "scenario_id" => scenario.id,
                "repetition" => repetition,
                "language" => "julia",
                "solver" => solver_id,
                "git_commit" => _git_commit(root),
                "machine" => _machine_description(),
                "precision" => T === Float32 ? "float32" : "float64",
                "resolution" => collect(resolution),
                "bounds" => [collect(pair) for pair in scenario.domain.bounds],
                "periodic_axes" => [string(axis) for axis in scenario.domain.periodic_axes],
                "reynolds" => Float64(scenario.reynolds),
                "effective_reynolds" => get(
                    diagnostic_values,
                    "effective_reynolds",
                    Float64(scenario.reynolds),
                ),
                "solver_configuration" => _benchmark_solver_configuration(scenario),
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
                "memory_measurement" => "rss",
                "runtime_startup_seconds" => nothing,
                "worker_startup_seconds" => nothing,
                "substeps" => total_substeps,
                "final_state_revision" => solver === nothing ? 0 : state_revision(solver),
                "diagnostic_state_revision" => diagnostic_revision,
                "last_step" => _benchmark_step(last_report),
                "diagnostics" => diagnostic_values,
                "success" => success,
                "failure" => failure,
                "warnings" => sort!(unique(warnings)),
            )
            validate_benchmark_result(result, schema_path)
            stem = "$solver_id-$(resolution[1])x$(resolution[2])-r$repetition"
            _write_json(joinpath(destination, "$stem.json"), result)
            matrix.save_snapshots && success && solver !== nothing &&
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
    schema_path = joinpath(find_repository_root(@__DIR__), "spec", "result.schema.json")
    for (root, _, files) in walkdir(directory), file in sort(files)
        endswith(file, ".json") || continue
        value = try
            JSON3.read(read(joinpath(root, file), String), Dict{String,Any})
        catch
            continue
        end
        if get(value, "schema_version", 0) == 1 && haskey(value, "solver")
            validate_benchmark_result(value, schema_path)
            push!(results, value)
        end
    end
    return results
end

const _BENCHMARK_IDENTITY_FIELDS = (
    "bounds",
    "periodic_axes",
    "reynolds",
    "effective_reynolds",
    "solver_configuration",
    "freestream",
    "foil",
    "control_history",
    "requested_duration",
    "output_dt",
    "seed",
)

function _benchmark_identity_equal(left, right, precision::AbstractString)
    if left isa Bool || right isa Bool
        return typeof(left) === typeof(right) && left == right
    elseif left isa Integer && right isa Integer
        return left == right
    elseif left isa Real && right isa Real
        tolerance = precision == "float32" ? 2.0e-6 : 2.0e-12
        return isapprox(Float64(left), Float64(right); rtol = tolerance, atol = tolerance)
    elseif left isa AbstractDict && right isa AbstractDict
        keys(left) == keys(right) || return false
        return all(_benchmark_identity_equal(left[key], right[key], precision) for key in keys(left))
    elseif left isa AbstractVector && right isa AbstractVector
        length(left) == length(right) || return false
        return all(_benchmark_identity_equal(a, b, precision) for (a, b) in zip(left, right))
    end
    return typeof(left) === typeof(right) && left == right
end

function _assert_matched_benchmark_identities(results::Vector{Dict{String,Any}})
    signatures = Dict{NTuple{5,String},Vector{Any}}()
    for result in results
        key = (
            String(result["benchmark_matrix_id"]),
            String(result["scenario_id"]),
            String(result["precision"]),
            JSON3.write(result["resolution"]),
            String(result["solver"]),
        )
        signature = [result[field] for field in _BENCHMARK_IDENTITY_FIELDS]
        previous = get!(signatures, key, signature)
        _benchmark_identity_equal(previous, signature, String(result["precision"])) || throw(ArgumentError(
            "benchmark artifacts reuse a matrix/scenario/resolution identity with " *
            "different physical inputs",
        ))
    end
    return nothing
end

function _assert_complete_benchmark_matrices(results::Vector{Dict{String,Any}})
    root = find_repository_root(@__DIR__)
    matrix_paths = Dict{String,String}()
    for path in readdir(joinpath(root, "benchmark-matrices"); join = true)
        endswith(path, ".json") || continue
        document = JSON3.read(read(path, String), Dict{String,Any})
        haskey(document, "id") && (matrix_paths[String(document["id"])] = path)
    end
    grouped = Dict{Tuple{String,String},Vector{Dict{String,Any}}}()
    for result in results
        key = (String(result["benchmark_matrix_id"]), String(result["language"]))
        push!(get!(grouped, key, Dict{String,Any}[]), result)
    end
    for ((matrix_id, language), selected) in grouped
        haskey(matrix_paths, matrix_id) ||
            throw(ArgumentError("cannot verify completeness of unknown matrix $matrix_id"))
        matrix = load_benchmark_matrix(matrix_paths[matrix_id])
        expected = Set(
            (solver, resolution, repetition)
            for solver in matrix.solvers
            for resolution in matrix.resolutions
            for repetition in 1:matrix.repetitions
        )
        observed_values = [
            (
                String(result["solver"]),
                (Int(result["resolution"][1]), Int(result["resolution"][2])),
                Int(result["repetition"]),
            )
            for result in selected
        ]
        observed = Set(observed_values)
        length(observed) == length(observed_values) ||
            throw(ArgumentError("duplicate $language artifacts for matrix $matrix_id"))
        missing = setdiff(expected, observed)
        extra = setdiff(observed, expected)
        isempty(missing) && isempty(extra) || throw(ArgumentError(
            "incomplete $language artifacts for matrix $matrix_id: " *
            "missing=$(collect(missing)) extra=$(collect(extra))",
        ))
    end
    return nothing
end

function format_benchmark_comparison(directory::AbstractString; require_complete::Bool = false)
    results = collect_benchmark_results(directory)
    isempty(results) && return "No benchmark result JSON files found."
    _assert_matched_benchmark_identities(results)
    require_complete && _assert_complete_benchmark_matrices(results)
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
