#!/usr/bin/env julia

using FoilBenchJulia

function main(arguments::Vector{String})
    root = find_repository_root(@__DIR__)
    scenario = load_scenario(joinpath(root, "scenarios", "airfoil", "default.json"))
    minimum_rate = length(arguments) >= 1 ? parse(Float64, arguments[1]) : 10.0
    failed = false
    for solver_id in solver_ids()
        solver = create_solver(solver_id, scalar_type(scenario))
        initialize!(solver, scenario, NacaFoil(scenario.foil), scenario.seed)
        advance!(solver, control_at(scenario, scenario.output_dt), scenario.output_dt)
        timings = Float64[]
        for step in 2:8
            started = time_ns()
            advance!(solver, control_at(scenario, step * scenario.output_dt), scenario.output_dt)
            push!(timings, (time_ns() - started) / 1.0e9)
        end
        rate = length(timings) / sum(timings)
        println(rpad(solver_id, 18), round(rate; digits = 2), " solver steps/s")
        failed |= rate < minimum_rate
    end
    failed && error("one or more solvers missed the $(minimum_rate) steps/s preview gate")
    return nothing
end

main(ARGS)
