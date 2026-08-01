module FoilBenchJulia

using JSON3
using LinearAlgebra
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
include("projection.jl")
include("advection.jl")
include("metrics.jl")
include("lbm_kernels.jl")
include("stable_fluids.jl")
include("lbm.jl")
include("viewer/model.jl")
include("viewer/worker.jl")

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
export StableFluidsSolver
export D2Q9_C
export D2Q9_OPPOSITE
export D2Q9_W
export LBMScaling
export LBMSolver
export advance!
export advect_faces
export advect_faces_skew_rk2
export advect_velocity
export apply_domain_boundaries!
export canonical_to_cell
export cell_centers
export cell_to_canonical
export cell_to_faces
export cell_velocity
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
export implicit_diffuse_scalar
export implicit_diffuse_velocity
export initialize!
export kinetic_energy
export lbm_equilibrium
export lbm_macroscopic
export lbm_scaling
export lbm_trt_collision
export load_canonical_state
export load_scenario
export momentum
export next_float32!
export next_uint32!
export normals
export nx
export ny
export nz
export option
export project_faces!
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
export solve_masked_poisson
export solid_leakage
export solid_mask
export supports
export surfaces
export reference_speed
export vorticity
export wake_width
export wall_velocity_grid
export AdjustReynoldsCommand
export AdjustBlendCommand
export ReleaseAngleCommand
export ResetReynoldsCommand
export ResetViewerCommand
export SetAngleCommand
export StopViewerCommand
export SwitchSolverCommand
export ToggleCropCommand
export TogglePauseCommand
export ToggleTracerCommand
export ToggleVorticityCommand
export TracerState
export ViewerModel
export ViewerSnapshot
export ViewerWorker
export advance_tracers!
export adjust_reynolds!
export adjust_blend!
export close!
export enqueue!
export foil_outline
export latest_snapshot
export path_segments
export release_angle!
export set_angle!
export snapshot
export start!
export toggle_crop!
export toggle_pause!
export toggle_tracer_mode!
export toggle_vorticity!
export update!
export reset_reynolds!
export reset_viewer!
export switch_solver!

end
