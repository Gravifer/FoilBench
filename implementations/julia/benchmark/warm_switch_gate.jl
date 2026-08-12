#!/usr/bin/env julia

using FoilBenchJulia
using JSON3

function main()
    root = find_repository_root(@__DIR__)
    fixture = JSON3.read(read(joinpath(root, "spec", "conformance", "fullsize-acceptance.json"), String))
    gate = fixture.warm_switch
    scenario = load_scenario(joinpath(root, String(gate.scenario)))
    Tuple(gate.resolution) == scenario.domain.resolution ||
        error("warm-switch fixture resolution disagrees with its scenario")
    identifiers = ("stable-fluids", "lbm-d2q9", "pic-flip")
    for angle in gate.angles_degrees, source in identifiers, destination in identifiers
        source == destination && continue
        model = ViewerModel(
            scenario;
            solver_id = source,
            tracer_count = 12,
            history_length = 3,
        )
        set_angle!(model, Float64(angle), 1.0)
        update!(model)
        accepted(switch_solver!(model, destination)) ||
            error("warm switch rejected: $source -> $destination at $angle")
        all(isfinite, export_state(model.solver).velocity) ||
            error("warm switch produced non-finite state")
        println("passed $source -> $destination at $(Float64(angle)) degrees")
    end
    return nothing
end

main()
