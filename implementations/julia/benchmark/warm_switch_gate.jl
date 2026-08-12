#!/usr/bin/env julia

using FoilBenchJulia
using JSON3

struct RejectingImportSolver{T<:AbstractFloat} <: AbstractFlowSolver{2,T}
    inner::AbstractFlowSolver{2,T}
end

struct FailingAdvanceSolver{T<:AbstractFloat} <: AbstractFlowSolver{2,T}
    inner::AbstractFlowSolver{2,T}
end

for Wrapper in (:RejectingImportSolver, :FailingAdvanceSolver)
    @eval begin
        FoilBenchJulia.solver_info(solver::$Wrapper) = solver_info(solver.inner)
        FoilBenchJulia.reynolds(solver::$Wrapper) = reynolds(solver.inner)
        FoilBenchJulia.state_revision(solver::$Wrapper) = state_revision(solver.inner)
        FoilBenchJulia.initialize!(solver::$Wrapper, scenario, geometry, seed::Integer) =
            initialize!(solver.inner, scenario, geometry, seed)
        FoilBenchJulia.restart!(solver::$Wrapper, scenario, geometry, seed::Integer, start::RestartState) =
            restart!(solver.inner, scenario, geometry, seed, start)
        FoilBenchJulia.set_reynolds!(solver::$Wrapper, selected::Real) =
            set_reynolds!(solver.inner, selected)
        FoilBenchJulia.sample_velocity(solver::$Wrapper, points::AbstractMatrix) =
            sample_velocity(solver.inner, points)
        FoilBenchJulia.export_state(solver::$Wrapper) = export_state(solver.inner)
        FoilBenchJulia.diagnostics(solver::$Wrapper) = diagnostics(solver.inner)
        FoilBenchJulia.interactive_tuning(solver::$Wrapper) = interactive_tuning(solver.inner)
        FoilBenchJulia.adjust_interactive_tuning!(solver::$Wrapper, direction::Integer) =
            adjust_interactive_tuning!(solver.inner, direction)
        FoilBenchJulia.apply_interactive_tuning!(solver::$Wrapper, value::FoilBenchJulia.InteractiveTuningValue) =
            apply_interactive_tuning!(solver.inner, value)
    end
end

FoilBenchJulia.advance!(solver::RejectingImportSolver, control::ControlState, target_dt::Real) =
    advance!(solver.inner, control, target_dt)

function FoilBenchJulia.import_state!(
    ::RejectingImportSolver,
    state,
    control::ControlState,
)
    state, control
    return ImportOutcome(
        :rejected,
        :nonfinite_state;
        stage = Symbol("canonical-import"),
    )
end

FoilBenchJulia.import_state!(solver::FailingAdvanceSolver, state, control::ControlState) =
    import_state!(solver.inner, state, control)

function FoilBenchJulia.advance!(
    ::FailingAdvanceSolver,
    control::ControlState,
    target_dt::Real,
)
    control, target_dt
    throw(NumericalFailure(
        :stability_limit,
        "injected fresh-fallback validation failure",
        Symbol("time-mapping"),
    ))
end

function fallback_factory(::Type{T}, destination::String; fail_fresh_step::Bool) where {T}
    destination_creations = Ref(0)
    function factory(identifier::AbstractString)
        solver = create_solver(String(identifier), T)
        identifier == destination || return solver
        destination_creations[] += 1
        destination_creations[] == 1 && return RejectingImportSolver{T}(solver)
        fail_fresh_step && return FailingAdvanceSolver{T}(solver)
        return solver
    end
    return factory, destination_creations
end

function validate_fallback(
    scenario::Scenario{2,T},
    angle::Real,
    source::String,
    destination::String;
    fail_fresh_step::Bool,
) where {T}
    factory, destination_creations = fallback_factory(
        T,
        destination;
        fail_fresh_step,
    )
    model = ViewerModel(
        scenario;
        solver_id = source,
        tracer_count = 12,
        history_length = 3,
        solver_factory = factory,
    )
    set_angle!(model, angle, 1.0)
    update!(model)
    source_solver = model.solver
    source_time = model.simulation_time
    source_epoch = model.solver_epoch
    source_positions = copy(model.tracers.positions)
    source_generations = copy(model.tracers.generations)
    source_counters = copy(model.tracers.recycle_counters)
    source_reynolds = reynolds(model.solver)

    outcome = switch_solver!(model, destination)
    if fail_fresh_step
        accepted(outcome) && error("fresh fallback committed a failing tentative destination")
        model.solver === source_solver || error("failed fresh fallback replaced the valid source")
        model.simulation_time == source_time || error("failed fresh fallback changed time")
        model.solver_epoch == source_epoch || error("failed fresh fallback changed solver epoch")
        model.tracers.positions == source_positions || error("failed fresh fallback changed tracers")
        model.tracers.generations == source_generations || error("failed fresh fallback changed generations")
        model.tracers.recycle_counters == source_counters || error("failed fresh fallback changed tracer counters")
    else
        accepted(outcome) || error("fresh fallback rejected a valid tentative destination")
        solver_info(model.solver).id == destination || error("fresh fallback selected the wrong destination")
        model.simulation_time > source_time || error("successful fresh fallback did not commit one step")
        model.solver_epoch == source_epoch + 1 || error("successful fresh fallback did not advance the epoch")
        model.recovery_reason == :nonfinite_state || error("successful fresh fallback omitted its reason")
        model.recovery_stage == Symbol("warm-import-fallback") || error("successful fresh fallback omitted its stage")
        reynolds(model.solver) == source_reynolds || error("successful fresh fallback changed Reynolds")
        all(isfinite, export_state(model.solver).velocity) || error("fresh fallback produced non-finite state")
        expected = source_counters[:forced_recovery] + size(model.tracers.positions, 2)
        model.tracers.recycle_counters[:forced_recovery] == expected ||
            error("successful fresh fallback did not reseed tracers exactly once")
    end
    destination_creations[] == 2 || error("fresh fallback did not construct exactly two destinations")
    return nothing
end

function main()
    root = find_repository_root(@__DIR__)
    fixture = JSON3.read(read(joinpath(root, "spec", "conformance", "fullsize-acceptance.json"), String))
    gate = fixture.warm_switch
    scenario = load_scenario(joinpath(root, String(gate.scenario)))
    Tuple(gate.resolution) == scenario.domain.resolution ||
        error("warm-switch fixture resolution disagrees with its scenario")
    Bool(gate.require_all_directed_pairs) ||
        error("Revision 4 requires every directed warm-switch pair")
    Bool(gate.validate_fresh_fallback_first_step) ||
        error("Revision 4 requires tentative fresh-fallback validation")
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
        println("passed warm $source -> $destination at $(Float64(angle)) degrees")
    end
    for angle in gate.angles_degrees, (index, destination) in enumerate(identifiers)
        source = identifiers[mod1(index + 1, length(identifiers))]
        validate_fallback(scenario, Float64(angle), source, destination; fail_fresh_step = false)
        validate_fallback(scenario, Float64(angle), source, destination; fail_fresh_step = true)
        println("passed fresh fallback transactions -> $destination at $(Float64(angle)) degrees")
    end
    return nothing
end

main()
