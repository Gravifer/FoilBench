module FoilBenchJulia

using JSON3
using LinearAlgebra
using NPZ
using Printf
using StaticArrays
using Dates

include("rng.jl")
include("geometry.jl")
include("contracts.jl")
include("schema_validation.jl")
include("scenario.jl")
include("state_io.jl")
include("solver_validation.jl")
include("geometry_grid.jl")
include("interpolation.jl")
include("grid.jl")
include("projection.jl")
include("advection.jl")
include("metrics.jl")
include("lbm_kernels.jl")
include("pic_kernels.jl")
include("stable_fluids.jl")
include("lbm.jl")
include("pic_flip.jl")
include("benchmarking.jl")
include("chaotic_experiments.jl")
include("viewer/model.jl")
include("viewer/worker.jl")

export AbstractFlowSolver
export ImportOutcome
export InteractiveTuning
export NumericalFailure
export RestartState
export ReynoldsOutcome
export accepted
export CanonicalFlowState
export foil_contains
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
export PicFlipSolver
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
export interactive_tuning
export adjust_interactive_tuning!
export apply_interactive_tuning!
export dimension
export divergence_l2
export dx
export dy
export enforce_solid_faces!
export enstrophy
export export_state
export face_divergence
export native_divergence_linf
export faces_to_cell
export solid_face_leakage
export import_state!
export implicit_diffuse_scalar
export implicit_diffuse_velocity
export initialize!
export restart!
export kinetic_energy
export maximum_radius
export lbm_equilibrium
export lbm_macroscopic
export lbm_scaling
export lbm_trt_collision
export grid_to_particle
export faces_to_particle
export faces_to_particle!
export particle_cell_counts
export particle_cell_ids
export particle_to_grid
export particle_to_faces
export quadratic_bspline_weight
export load_canonical_state
export load_scenario
export momentum
export pic_flip_blend
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
export state_revision
export rk2_backtrace
export sample_scalar
export sample_staggered_scalar
export sample_velocity
export sample_velocity_field
export scalar_type
export save_canonical_state
export set_reynolds!
export set_pic_flip_blend!
export set_stable_transport_mode!
export solver_info
export stable_transport_mode
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
export wall_velocity
export AdjustReynoldsCommand
export AdjustTuningCommand
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
export ToggleDiagnosticsCommand
export TracerState
export PresentationState
export ViewerModel
export ViewerSnapshot
export ViewerWorker
export BenchmarkMatrix
export collect_benchmark_results
export create_solver
export describe_implementation
export find_repository_root
export format_benchmark_comparison
export load_benchmark_matrix
export run_benchmark_matrix
export scenario_with_run
export solver_ids
export validate_benchmark_result
export WakeSweepCase
export chaotic_scenario
export run_chaos_sensitivity
export run_chaotic_wake_case
export temporal_spectral_statistics
export advance_tracers!
export adjust_reynolds!
export adjust_tuning!
export close!
export enqueue!
export foil_outline
export latest_snapshot
export wait_for_command
export wait_for_revision
export path_segments
export reseed_tracers!
export rapid_drag_attempted
export requested_tip_speed_ratio
export recover_solver!
export enable_pose_only_drag!
export release_angle!
export set_angle!
export snapshot
export start!
export toggle_crop!
export toggle_pause!
export toggle_tracer_mode!
export toggle_vorticity!
export toggle_diagnostics!
export viewer_session_state
export update!
export reset_reynolds!
export reset_viewer!
export switch_solver!

end
