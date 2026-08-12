#!/usr/bin/env julia

using FoilBenchJulia
using JSON3
using Statistics

function main(arguments::Vector{String})
    root = find_repository_root(@__DIR__)
    fixture = JSON3.read(read(joinpath(root, "spec", "conformance", "fullsize-acceptance.json"), String))
    scenario = load_scenario(joinpath(root, String(fixture.preview.scenario)))
    Tuple(fixture.preview.resolution) == scenario.domain.resolution ||
        error("preview fixture resolution disagrees with its scenario")
    minimum_rate = length(arguments) >= 1 ? parse(Float64, arguments[1]) :
        Float64(fixture.preview.minimum_warmed_solver_steps_per_second)
    failed = false
    for solver_id in solver_ids()
        solver = create_solver(solver_id, scalar_type(scenario))
        initialize!(solver, scenario, NacaFoil(scenario.foil), scenario.seed)
        advance!(solver, control_at(scenario, scenario.output_dt), scenario.output_dt)
        for step in 2:7
            advance!(solver, control_at(scenario, step * scenario.output_dt), scenario.output_dt)
        end
        timings = Float64[]
        for step in 8:27
            started = time_ns()
            advance!(solver, control_at(scenario, step * scenario.output_dt), scenario.output_dt)
            push!(timings, (time_ns() - started) / 1.0e9)
        end
        rate = inv(median(timings))
        println(rpad(solver_id, 18), round(rate; digits = 2), " median solver steps/s")
        failed |= rate < minimum_rate
    end
    failed && error("one or more solvers missed the $(minimum_rate) steps/s preview gate")
    return nothing
end

main(ARGS)
