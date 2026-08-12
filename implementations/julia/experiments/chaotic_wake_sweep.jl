#!/usr/bin/env julia

using FoilBenchJulia
using JSON3

root = find_repository_root(@__DIR__)
scenario_path = length(ARGS) >= 1 ? ARGS[1] : joinpath(root, "scenarios", "airfoil", "chaotic-experimental.json")
output_path = length(ARGS) >= 2 ? ARGS[2] : nothing
duration = length(ARGS) >= 3 ? parse(Float64, ARGS[3]) : 12.0
burn_in = length(ARGS) >= 4 ? parse(Float64, ARGS[4]) : 4.0
base = load_scenario(scenario_path)
cases = if length(ARGS) >= 8
    [WakeSweepCase(
        parse(Float64, ARGS[5]),
        parse(Float64, ARGS[6]),
        (parse(Int, ARGS[7]), parse(Int, ARGS[8])),
    )]
else
    [WakeSweepCase(reynolds, angle, (160, 96)) for
        reynolds in (1_000.0, 10_000.0), angle in (25.0, 35.0)]
end
function envelope(selected, raw)
    metric_names = (
        "probe_rms", "spectral_entropy", "dominant_power_fraction",
        "broadband_power_fraction", "decorrelation_time", "enstrophy_mean",
        "enstrophy_coefficient_of_variation", "maximum_speed",
        "vorticity_small_scale_fraction",
    )
    return Dict{String,Any}(
        "schema_version" => 1,
        "contract_id" => "foilbench-phase2-v1",
        "experiment" => "chaotic-wake-sweep",
        "language" => "julia",
        "solver" => "stable-fluids",
        "scenario" => "chaotic-wake-re$(selected.reynolds)-a$(selected.angle_degrees)-$(selected.resolution[1])x$(selected.resolution[2])",
        "parameters" => Dict(
            "reynolds" => selected.reynolds,
            "angle_degrees" => selected.angle_degrees,
            "resolution" => collect(selected.resolution),
            "duration" => duration,
            "burn_in" => burn_in,
        ),
        "metrics" => Dict(name => raw[name] for name in metric_names),
        "wall_seconds" => raw["wall_seconds"],
    )
end
results = [envelope(selected, run_chaotic_wake_case(base, selected; duration, burn_in)) for selected in vec(cases)]
JSON3.pretty(stdout, results)
println()
if output_path !== nothing
    mkpath(dirname(output_path))
    open(output_path, "w") do io
        JSON3.pretty(io, results)
    end
end
