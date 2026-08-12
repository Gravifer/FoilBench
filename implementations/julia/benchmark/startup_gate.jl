#!/usr/bin/env julia

using FoilBenchJulia
using JSON3

function main()
    root = find_repository_root(@__DIR__)
    fixture = JSON3.read(read(joinpath(root, "spec", "conformance", "fullsize-acceptance.json"), String))
    gate = fixture.startup
    scenario = load_scenario(joinpath(root, String(gate.scenario)))
    Tuple(gate.resolution) == scenario.domain.resolution ||
        error("startup fixture resolution disagrees with its scenario")
    steps = Int(gate.steps)
    steps >= 1 || error("startup gate requires at least one step")
    for solver_id in String.(gate.solvers)
        solver = create_solver(solver_id, scalar_type(scenario))
        initialize!(solver, scenario, NacaFoil(scenario.foil), scenario.seed)
        for step in 1:steps
            target_time = step * scenario.output_dt
            report = advance!(solver, control_at(scenario, target_time), scenario.output_dt)
            isapprox(report.advanced_dt, scenario.output_dt; rtol = 0, atol = eps(scenario.output_dt) * 8) ||
                error("$solver_id violated requested startup time")
        end
        state = export_state(solver)
        all(isfinite, state.velocity) || error("$solver_id produced a non-finite startup state")
        diagnostics(solver).state_revision == state_revision(solver) ||
            error("$solver_id produced stale startup diagnostics")
        println(rpad(solver_id, 18), "passed $steps startup step(s)")
    end
    return nothing
end

main()
