#!/usr/bin/env julia

using FoilBenchJulia
using JSON3

length(ARGS) == 1 || error("usage: interchange_gate.jl RESULTS_ROOT")
root = find_repository_root(@__DIR__)
results = abspath(ARGS[1])
scenario_base = load_scenario(joinpath(root, "scenarios", "airfoil", "default.json"))
expected = Set(
    (language, solver_id)
    for language in ("python", "julia", "typescript", "rust")
    for solver_id in solver_ids()
)
observed = Set{Tuple{String,String}}()
for (directory, _, files) in walkdir(results)
    "manifest.json" in files || continue
    manifest = JSON3.read(read(joinpath(directory, "manifest.json"), String))
    source_language = Int(manifest.schema_version) == 2 ?
        String(manifest.producer.implementation) : String(manifest.source_language)
    source = (source_language, String(manifest.source_solver))
    source in observed && error("duplicate canonical snapshot from $source")
    push!(observed, source)
    state = load_canonical_state(directory)
    domain = DomainSpec(
        scenario_base.domain.bounds,
        state.resolution,
        scenario_base.domain.periodic_axes,
    )
    scenario = Scenario(
        scenario_base.schema_version, scenario_base.id, domain,
        scenario_base.reynolds, scenario_base.freestream, scenario_base.foil,
        scenario_base.controls, scenario_base.duration, scenario_base.output_dt,
        scenario_base.precision, scenario_base.seed, copy(scenario_base.solver_options),
    )
    control = ControlState(
        state.time, state.angle_degrees, state.angular_velocity_degrees,
    )
    geometry = NacaFoil(scenario.foil)
    for destination_id in solver_ids()
        destination = create_solver(destination_id, scalar_type(scenario))
        initialize!(destination, scenario, geometry, scenario.seed)
        outcome = import_state!(destination, state, control)
        accepted(outcome) || error(
            "Julia rejected $source in $destination_id: $(outcome.reason) at $(outcome.stage)",
        )
    end
end
observed == expected || error(
    "canonical producer roster mismatch: missing=$(setdiff(expected, observed)) " *
    "extra=$(setdiff(observed, expected))",
)
println("Julia imported all 36 cross-language canonical conversions")
