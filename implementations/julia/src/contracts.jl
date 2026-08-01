abstract type AbstractFlowSolver{D,T<:AbstractFloat} end

struct SolverInfo
    id::String
    display_name::String
    dimensions::Tuple{Vararg{Int}}
    supports_moving_boundary::Bool
    acceleration::Symbol
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
end

struct ImportReport
    source_solver::String
    destination_solver::String
    discarded_state::Vector{String}
    warnings::Vector{String}
end

struct Diagnostics
    values::Dict{String,Float64}
    warnings::Vector{String}
end

solver_info(::AbstractFlowSolver) = error("solver_info is not implemented")
reynolds(::AbstractFlowSolver) = error("reynolds is not implemented")
initialize!(::AbstractFlowSolver, scenario, geometry, ::Integer) =
    error("initialize! is not implemented for $(typeof(scenario)) and $(typeof(geometry))")
set_reynolds!(::AbstractFlowSolver, ::Real) = error("set_reynolds! is not implemented")
advance!(::AbstractFlowSolver, ::ControlState, ::Real) = error("advance! is not implemented")
sample_velocity(::AbstractFlowSolver, ::AbstractMatrix) =
    error("sample_velocity is not implemented")
export_state(::AbstractFlowSolver) = error("export_state is not implemented")
import_state!(::AbstractFlowSolver, state, ::ControlState) =
    error("import_state! is not implemented")
diagnostics(::AbstractFlowSolver) = error("diagnostics is not implemented")

function supports(info::SolverInfo, scenario)
    return dimension(scenario) in info.dimensions
end

function require_supported(info::SolverInfo, scenario)
    supports(info, scenario) && return nothing
    supported = join(info.dimensions, ", ")
    throw(ArgumentError("$(info.id) supports dimensions [$supported], not $(dimension(scenario))D"))
end
