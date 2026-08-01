module FoilBenchJulia

using JSON3
using NPZ
using StaticArrays

include("rng.jl")
include("geometry.jl")
include("scenario.jl")
include("state_io.jl")

export CanonicalFlowState
export ControlKeyframe
export DomainSpec
export FoilSpec
export NacaFoil
export PCG32
export Scenario
export load_canonical_state
export load_scenario
export next_float32!
export next_uint32!
export normals
export signed_distance
export surfaces

end
