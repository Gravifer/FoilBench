module FoilBenchJulia

using JSON3
using NPZ
using StaticArrays

include("rng.jl")
include("geometry.jl")
include("contracts.jl")
include("scenario.jl")
include("state_io.jl")

export AbstractFlowSolver
export CanonicalFlowState
export ControlKeyframe
export ControlState
export Diagnostics
export DomainSpec
export FoilSpec
export ImportReport
export NacaFoil
export PCG32
export Scenario
export SolverInfo
export StepReport
export advance!
export control_at
export diagnostics
export dimension
export export_state
export import_state!
export initialize!
export load_canonical_state
export load_scenario
export next_float32!
export next_uint32!
export normals
export require_supported
export reynolds
export sample_velocity
export scalar_type
export save_canonical_state
export set_reynolds!
export solver_info
export signed_distance
export supports
export surfaces

end
