abstract type AbstractFlowSolver{D,T<:AbstractFloat} end

struct SolverInfo
    id::String
    display_name::String
    dimensions::Tuple{Vararg{Int}}
    supports_moving_boundary::Bool
    supported_precisions::Tuple{Vararg{Symbol}}
    acceleration::Symbol
end

const InteractiveTuningValue = Union{String,Float64}

struct InteractiveTuning
    id::String
    label::String
    value::InteractiveTuningValue
    display_value::String
    can_decrease::Bool
    can_increase::Bool
end

struct ControlState{T<:AbstractFloat}
    time::T
    angle_degrees::T
    angular_velocity_degrees::T
end

struct StepReport{T<:AbstractFloat}
    requested_dt::T
    advanced_dt::T
    substeps::Int
    max_speed::T
    warnings::Vector{String}
    state_revision::Int
    evidence::Dict{String,Any}
end

StepReport(requested_dt::T, advanced_dt::T, substeps::Int, max_speed::T, warnings::Vector{String}) where {T<:AbstractFloat} =
    StepReport(requested_dt, advanced_dt, substeps, max_speed, warnings, 0, Dict{String,Any}())

struct RestartState{T<:AbstractFloat}
    time::T
    angle_degrees::T
    reynolds::T
end

struct ReynoldsOutcome{T<:AbstractFloat}
    requested::T
    effective::T
    warnings::Vector{String}
end

struct ImportReport
    source_solver::String
    destination_solver::String
    discarded_state::Vector{String}
    warnings::Vector{String}
end

struct ImportOutcome
    status::Symbol
    reason::Symbol
    report::Union{Nothing,ImportReport}
    warnings::Vector{String}
    stage::Symbol
    evidence::Dict{String,Any}

    function ImportOutcome(
        status::Symbol,
        reason::Symbol;
        report::Union{Nothing,ImportReport} = nothing,
        warnings::Vector{String} = String[],
        stage::Symbol = :none,
        evidence::Dict{String,Any} = Dict{String,Any}(),
    )
        status in (:accepted, :rejected) || throw(ArgumentError("invalid import status"))
        reason in (
            :none,
            :excessive_velocity,
            :stability_limit,
            :nonfinite_state,
            :convergence_failure,
            :incompatible_geometry,
            :incompatible_domain,
            :projection_failure,
            :invalid_density,
            :invalid_population,
            :invalid_relaxation,
            :transfer_failure,
            :postcondition_failure,
            :time_contract_failure,
            :unsupported_conversion,
        ) || throw(ArgumentError("invalid import failure reason"))
        status == :accepted && reason != :none &&
            throw(ArgumentError("accepted import cannot have a failure reason"))
        status == :rejected && reason == :none &&
            throw(ArgumentError("rejected import needs a failure reason"))
        new(status, reason, report, warnings, stage, evidence)
    end
end

struct NumericalFailure <: Exception
    reason::Symbol
    detail::String
    stage::Symbol
    evidence::Dict{String,Any}

    function NumericalFailure(
        reason::Symbol,
        detail::AbstractString,
        stage::Symbol = :postcondition,
        evidence::Dict{String,Any} = Dict{String,Any}(),
    )
        reason in (
            :excessive_velocity,
            :stability_limit,
            :nonfinite_state,
            :convergence_failure,
            :projection_failure,
            :invalid_density,
            :invalid_population,
            :invalid_relaxation,
            :transfer_failure,
            :postcondition_failure,
            :time_contract_failure,
            :incompatible_geometry,
            :incompatible_domain,
            :unsupported_conversion,
        ) || throw(ArgumentError("invalid numerical failure reason"))
        new(reason, String(detail), stage, evidence)
    end
end

Base.showerror(io::IO, failure::NumericalFailure) = print(io, failure.detail)

accepted(outcome::ImportOutcome) = outcome.status == :accepted

struct Diagnostics
    values::Dict{String,Float64}
    warnings::Vector{String}
    state_revision::Int
end

Diagnostics(values::Dict{String,Float64}, warnings::Vector{String}) = Diagnostics(values, warnings, 0)

solver_info(::AbstractFlowSolver) = error("solver_info is not implemented")
reynolds(::AbstractFlowSolver) = error("reynolds is not implemented")
state_revision(::AbstractFlowSolver) = error("state_revision is not implemented")
initialize!(::AbstractFlowSolver, scenario, geometry, ::Integer) =
    error("initialize! is not implemented for $(typeof(scenario)) and $(typeof(geometry))")
restart!(::AbstractFlowSolver, scenario, geometry, ::Integer, ::RestartState) =
    error("restart! is not implemented for $(typeof(scenario)) and $(typeof(geometry))")
set_reynolds!(::AbstractFlowSolver, ::Real) = error("set_reynolds! is not implemented")
advance!(::AbstractFlowSolver, ::ControlState, ::Real) = error("advance! is not implemented")
sample_velocity(::AbstractFlowSolver, ::AbstractMatrix) =
    error("sample_velocity is not implemented")
export_state(::AbstractFlowSolver) = error("export_state is not implemented")
import_state!(::AbstractFlowSolver, state, ::ControlState) =
    error("import_state! is not implemented")
diagnostics(::AbstractFlowSolver) = error("diagnostics is not implemented")
interactive_tuning(::AbstractFlowSolver) = nothing
adjust_interactive_tuning!(::AbstractFlowSolver, ::Integer) = nothing
apply_interactive_tuning!(::AbstractFlowSolver, ::InteractiveTuningValue) = nothing

function supports(info::SolverInfo, scenario)
    return dimension(scenario) in info.dimensions && scenario.precision in info.supported_precisions
end

function require_supported(info::SolverInfo, scenario)
    supports(info, scenario) && return nothing
    dimensions = join(info.dimensions, ", ")
    precisions = join(String.(info.supported_precisions), ", ")
    throw(ArgumentError(
        "$(info.id) supports dimensions [$dimensions] and precisions [$precisions], " *
        "not $(dimension(scenario))D/$(scenario.precision)",
    ))
end
