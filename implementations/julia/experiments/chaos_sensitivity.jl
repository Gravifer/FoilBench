#!/usr/bin/env julia

using FoilBenchJulia
using JSON3

root = find_repository_root(@__DIR__)
scenario_path = length(ARGS) >= 1 ? ARGS[1] : joinpath(root, "scenarios", "airfoil", "chaotic-experimental.json")
output_path = length(ARGS) >= 2 ? ARGS[2] : nothing
duration = length(ARGS) >= 3 ? parse(Float64, ARGS[3]) : 12.0
epsilon = length(ARGS) >= 4 ? parse(Float64, ARGS[4]) : 1.0e-4
base = load_scenario(scenario_path)
selected = WakeSweepCase(10_000.0, 35.0, (160, 96))
result = run_chaos_sensitivity(base, selected; duration, epsilon)
JSON3.pretty(stdout, result)
println()
if output_path !== nothing
    mkpath(dirname(output_path))
    open(output_path, "w") do io
        JSON3.pretty(io, result)
    end
end
