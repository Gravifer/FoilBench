#!/usr/bin/env julia

using FoilBenchJulia
using JSON3

root = find_repository_root(@__DIR__)
scenario_path = length(ARGS) >= 1 ? ARGS[1] : joinpath(root, "scenarios", "airfoil", "chaotic-experimental.json")
output_path = length(ARGS) >= 2 ? ARGS[2] : nothing
duration = length(ARGS) >= 3 ? parse(Float64, ARGS[3]) : 12.0
burn_in = length(ARGS) >= 4 ? parse(Float64, ARGS[4]) : 4.0
base = load_scenario(scenario_path)
cases = [WakeSweepCase(reynolds, angle, (160, 96)) for reynolds in (1_000.0, 10_000.0), angle in (25.0, 35.0)]
results = [run_chaotic_wake_case(base, selected; duration, burn_in) for selected in vec(cases)]
JSON3.pretty(stdout, results)
println()
if output_path !== nothing
    mkpath(dirname(output_path))
    open(output_path, "w") do io
        JSON3.pretty(io, results)
    end
end
