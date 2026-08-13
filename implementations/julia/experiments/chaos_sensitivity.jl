#!/usr/bin/env julia

using FoilBenchJulia
using JSON3

root = find_repository_root(@__DIR__)
scenario_path = length(ARGS) >= 1 ? ARGS[1] : joinpath(root, "scenarios", "airfoil", "chaotic-experimental.json")
output_path = length(ARGS) >= 2 ? ARGS[2] : nothing
duration = length(ARGS) >= 3 ? parse(Float64, ARGS[3]) : 12.0
epsilon = length(ARGS) >= 4 ? parse(Float64, ARGS[4]) : 1.0e-4
base = load_scenario(scenario_path)
reynolds = length(ARGS) >= 5 ? parse(Float64, ARGS[5]) : 10_000.0
angle = length(ARGS) >= 6 ? parse(Float64, ARGS[6]) : 35.0
resolution = length(ARGS) >= 8 ? (parse(Int, ARGS[7]), parse(Int, ARGS[8])) : (160, 96)
selected = WakeSweepCase(reynolds, angle, resolution)
raw = run_chaos_sensitivity(base, selected; duration, epsilon)
metric_names = (
    "initial_wake_rms_difference", "final_wake_rms_difference",
    "maximum_wake_rms_difference", "amplification", "finite_time_exponent",
    "exponential_fit_r_squared", "exponential_fit_samples",
)
result = Dict{String,Any}(
    "schema_version" => 2,
    "contract_id" => "foilbench-phase3-v1",
    "contract_revision" => 5,
    "experiment" => "chaotic-wake-sensitivity",
    "language" => "julia",
    "implementation" => "julia",
    "execution_target" => "native",
    "solver" => "stable-fluids",
    "scenario" => raw["scenario"],
    "parameters" => Dict(
        "reynolds" => selected.reynolds,
        "angle_degrees" => selected.angle_degrees,
        "resolution" => collect(selected.resolution),
        "duration" => duration,
        "epsilon" => epsilon,
    ),
    "metrics" => Dict(name => raw[name] for name in metric_names),
    "initialization" => raw["initialization"],
    "series" => Dict(
        "times" => raw["times"],
        "wake_rms_differences" => raw["wake_rms_differences"],
    ),
    "wall_seconds" => raw["wall_seconds"],
)
JSON3.pretty(stdout, result)
println()
if output_path !== nothing
    mkpath(dirname(output_path))
    open(output_path, "w") do io
        JSON3.pretty(io, result)
    end
end
