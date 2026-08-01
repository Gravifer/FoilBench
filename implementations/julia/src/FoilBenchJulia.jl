module FoilBenchJulia

using JSON3
using NPZ
using StaticArrays

include("rng.jl")
include("geometry.jl")
include("contracts.jl")
include("scenario.jl")
include("state_io.jl")
include("geometry_grid.jl")
include("interpolation.jl")
include("grid.jl")
include("metrics.jl")

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
export apply_domain_boundaries!
export canonical_to_cell
export cell_centers
export cell_to_canonical
export cell_to_faces
export control_at
export diagnostics
export dimension
export divergence_l2
export dx
export dy
export enforce_solid_faces!
export enstrophy
export export_state
export face_divergence
export faces_to_cell
export import_state!
export initialize!
export kinetic_energy
export load_canonical_state
export load_scenario
export momentum
export next_float32!
export next_uint32!
export normals
export nx
export ny
export nz
export recirculation_area
export require_supported
export reynolds
export rk2_backtrace
export sample_scalar
export sample_staggered_scalar
export sample_velocity
export sample_velocity_field
export scalar_type
export save_canonical_state
export set_reynolds!
export solver_info
export signed_distance
export solid_leakage
export solid_mask
export supports
export surfaces
export vorticity
export wake_width
export wall_velocity_grid

end
