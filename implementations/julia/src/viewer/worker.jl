abstract type ViewerCommand end
struct TogglePauseCommand <: ViewerCommand end
struct ToggleVorticityCommand <: ViewerCommand end
struct ToggleTracerCommand <: ViewerCommand end
struct ToggleCropCommand <: ViewerCommand end
struct ResetReynoldsCommand <: ViewerCommand end
struct ResetViewerCommand <: ViewerCommand end
struct StopViewerCommand <: ViewerCommand end
struct SetAngleCommand{T<:AbstractFloat} <: ViewerCommand
    angle_degrees::T
    elapsed::T
end
struct ReleaseAngleCommand <: ViewerCommand end
struct AdjustReynoldsCommand{T<:AbstractFloat} <: ViewerCommand
    decades::T
end
struct SwitchSolverCommand <: ViewerCommand
    solver_id::String
end
struct AdjustBlendCommand{T<:AbstractFloat} <: ViewerCommand
    amount::T
end

mutable struct ViewerWorker{T<:AbstractFloat}
    model::ViewerModel{T}
    commands::Channel{ViewerCommand}
    snapshots::Channel{ViewerSnapshot{T}}
    latest_angle::Base.RefValue{Union{Nothing,SetAngleCommand{T}}}
    angle_lock::ReentrantLock
    task::Union{Nothing,Task}
    running::Base.RefValue{Bool}
end

function ViewerWorker(model::ViewerModel{T}) where {T}
    return ViewerWorker(
        model,
        Channel{ViewerCommand}(32),
        Channel{ViewerSnapshot{T}}(1),
        Ref{Union{Nothing,SetAngleCommand{T}}}(nothing),
        ReentrantLock(),
        nothing,
        Ref(false),
    )
end

function _publish_latest!(worker::ViewerWorker, selected::ViewerSnapshot)
    isready(worker.snapshots) && take!(worker.snapshots)
    put!(worker.snapshots, selected)
    return nothing
end

function _apply_command!(worker::ViewerWorker, command::ViewerCommand)
    model = worker.model
    command isa TogglePauseCommand && toggle_pause!(model)
    command isa ToggleVorticityCommand && toggle_vorticity!(model)
    command isa ToggleTracerCommand && toggle_tracer_mode!(model)
    command isa ToggleCropCommand && toggle_crop!(model)
    command isa ResetReynoldsCommand && reset_reynolds!(model)
    command isa ResetViewerCommand && reset_viewer!(model)
    command isa SetAngleCommand && set_angle!(model, command.angle_degrees, command.elapsed)
    command isa ReleaseAngleCommand && release_angle!(model)
    command isa AdjustReynoldsCommand && adjust_reynolds!(model, command.decades)
    command isa SwitchSolverCommand && switch_solver!(model, command.solver_id)
    command isa AdjustBlendCommand && adjust_blend!(model, command.amount)
    command isa StopViewerCommand && (worker.running[] = false)
    return nothing
end

function _worker_loop(worker::ViewerWorker)
    worker.running[] = true
    _publish_latest!(worker, snapshot(worker.model))
    while worker.running[]
        started = time_ns()
        angle_command = lock(worker.angle_lock) do
            selected = worker.latest_angle[]
            worker.latest_angle[] = nothing
            selected
        end
        angle_command === nothing || _apply_command!(worker, angle_command)
        while isready(worker.commands)
            _apply_command!(worker, take!(worker.commands))
        end
        worker.running[] || break
        try
            _publish_latest!(worker, update!(worker.model))
        catch error
            worker.model.paused = true
            worker.model.status_message = "paused after $(typeof(error))"
            _publish_latest!(worker, snapshot(worker.model))
        end
        elapsed = (time_ns() - started) / 1.0e9
        sleep(max(0.0, 1 / 60 - elapsed))
    end
    return nothing
end

function start!(worker::ViewerWorker)
    worker.task === nothing || throw(ArgumentError("viewer worker is already started"))
    worker.task = Threads.@spawn _worker_loop(worker)
    return worker
end

function enqueue!(worker::ViewerWorker{T}, command::SetAngleCommand{T}) where {T}
    lock(worker.angle_lock) do
        worker.latest_angle[] = command
    end
    return command
end

enqueue!(worker::ViewerWorker, command::ViewerCommand) = put!(worker.commands, command)

function latest_snapshot(worker::ViewerWorker)
    return isready(worker.snapshots) ? take!(worker.snapshots) : nothing
end

function close!(worker::ViewerWorker)
    worker.task === nothing && return nothing
    enqueue!(worker, StopViewerCommand())
    wait(worker.task)
    worker.task = nothing
    return nothing
end
