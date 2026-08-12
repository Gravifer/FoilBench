#!/usr/bin/env julia

using FoilBenchJulia
using JSON3

function main()
    root = find_repository_root(@__DIR__)
    fixture = JSON3.read(read(joinpath(root, "spec", "conformance", "fullsize-acceptance.json"), String))
    gate = fixture.scheduled_checkpoints
    scenario = load_scenario(joinpath(root, String(gate.scenario)))
    Tuple(gate.resolution) == scenario.domain.resolution ||
        error("scheduled fixture resolution disagrees with its scenario")
    solver = create_solver(String(gate.solver), scalar_type(scenario))
    initialize!(solver, scenario, NacaFoil(scenario.foil), scenario.seed)
    checkpoint_steps = Dict(round(Int, Float64(value) / scenario.output_dt) => Float64(value) for value in gate.times)
    for step in 1:maximum(keys(checkpoint_steps))
        time = step * scenario.output_dt
        advance!(solver, control_at(scenario, time), scenario.output_dt)
        if haskey(checkpoint_steps, step)
            all(isfinite, export_state(solver).velocity) ||
                error("non-finite scheduled state at t=$time")
            println("passed scheduled checkpoint t=$time")
        end
    end
    return nothing
end

main()
