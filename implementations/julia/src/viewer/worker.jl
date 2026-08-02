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
    timestamp::Float64
end
struct ReleaseAngleCommand <: ViewerCommand end
struct AdjustReynoldsCommand{T<:AbstractFloat} <: ViewerCommand
    decades::T
end
struct SwitchSolverCommand <: ViewerCommand
    solver_id::String
end
struct AdjustTuningCommand{T<:AbstractFloat} <: ViewerCommand
    amount::T
end

struct QueuedViewerCommand
    sequence::UInt64
    command::ViewerCommand
end

mutable struct ViewerWorker{T<:AbstractFloat}
    model::ViewerModel{T}
    commands::Channel{QueuedViewerCommand}
    latest_angle::Base.RefValue{Union{Nothing,QueuedViewerCommand}}
    command_lock::ReentrantLock
    next_sequence::UInt64
    accepting_commands::Bool
    task::Union{Nothing,Task}
    running::Base.RefValue{Bool}
    wake_signal::Channel{Nothing}
    snapshot_lock::ReentrantLock
    latest::Base.RefValue{Union{Nothing,ViewerSnapshot{T}}}
    revision::UInt64
    applied_command::UInt64
    recovery_pending::Bool
    recent_failures::Vector{Float64}
end

function ViewerWorker(model::ViewerModel{T}) where {T}
    return ViewerWorker(
        model,
        Channel{QueuedViewerCommand}(256),
        Ref{Union{Nothing,QueuedViewerCommand}}(nothing),
        ReentrantLock(),
        UInt64(1),
        true,
        nothing,
        Ref(false),
        Channel{Nothing}(1),
        ReentrantLock(),
        Ref{Union{Nothing,ViewerSnapshot{T}}}(nothing),
        UInt64(0),
        UInt64(0),
        false,
        Float64[],
    )
end

function _clear_failure_history!(worker::ViewerWorker)
    empty!(worker.recent_failures)
    return nothing
end

function _record_failure!(worker::ViewerWorker, now::Float64)
    push!(worker.recent_failures, now)
    filter!(failure_time -> failure_time >= now - 5.0, worker.recent_failures)
    return length(worker.recent_failures)
end

function _with_revision(
    selected::ViewerSnapshot{T},
    revision::UInt64,
    applied_command::UInt64,
) where {T}
    return ViewerSnapshot(
        revision,
        applied_command,
        selected.time,
        selected.angle_degrees,
        selected.solver_id,
        selected.tracer_positions,
        selected.path_segments,
        selected.velocity,
        selected.vorticity,
        selected.diagnostics,
        selected.status,
        selected.paused,
        selected.vorticity_visible,
        selected.crop_enabled,
        selected.tracer_mode,
    )
end

function _publish_latest!(worker::ViewerWorker, selected::ViewerSnapshot)
    lock(worker.snapshot_lock) do
        worker.revision += 1
        worker.latest[] = _with_revision(selected, worker.revision, worker.applied_command)
    end
    return nothing
end

function _signal!(worker::ViewerWorker)
    isready(worker.wake_signal) || put!(worker.wake_signal, nothing)
    return nothing
end

function _apply_command!(worker::ViewerWorker, command::ViewerCommand)
    model = worker.model
    command isa TogglePauseCommand && toggle_pause!(model)
    command isa ToggleVorticityCommand && toggle_vorticity!(model)
    command isa ToggleTracerCommand && toggle_tracer_mode!(model)
    command isa ToggleCropCommand && toggle_crop!(model)
    if command isa ResetReynoldsCommand
        reset_reynolds!(model)
        worker.recovery_pending = false
        _clear_failure_history!(worker)
    end
    if command isa ResetViewerCommand
        reset_viewer!(model)
        worker.recovery_pending = false
        _clear_failure_history!(worker)
    end
    command isa SetAngleCommand && set_angle!(model, command.angle_degrees, command.timestamp)
    command isa ReleaseAngleCommand && release_angle!(model)
    if command isa AdjustReynoldsCommand
        adjust_reynolds!(model, command.decades)
        worker.recovery_pending = false
        _clear_failure_history!(worker)
    end
    if command isa SwitchSolverCommand
        switch_solver!(model, command.solver_id)
        worker.recovery_pending = false
        _clear_failure_history!(worker)
    end
    command isa AdjustTuningCommand && adjust_tuning!(model, command.amount)
    command isa StopViewerCommand && (worker.running[] = false)
    return nothing
end

function _drain_commands!(worker::ViewerWorker)
    queued = QueuedViewerCommand[]
    lock(worker.command_lock) do
        selected = worker.latest_angle[]
        worker.latest_angle[] = nothing
        selected === nothing || push!(queued, selected)
    end
    while isready(worker.commands)
        push!(queued, take!(worker.commands))
    end
    sort!(queued; by = command -> command.sequence)
    return queued
end

function _worker_loop(worker::ViewerWorker)
    worker.running[] = true
    _publish_latest!(worker, snapshot(worker.model))
    active_wall_started = time_ns()
    active_simulation_started = worker.model.simulation_time
    interactive_throughput = nothing
    while worker.running[]
        while isready(worker.wake_signal)
            take!(worker.wake_signal)
        end
        queued = _drain_commands!(worker)
        for selected in queued
            _apply_command!(worker, selected.command)
            worker.applied_command = max(worker.applied_command, selected.sequence)
        end
        if !isempty(queued)
            active_wall_started = time_ns()
            active_simulation_started = worker.model.simulation_time
            worker.model.metrics_warming && (interactive_throughput = nothing)
        end
        if !worker.running[]
            _publish_latest!(worker, snapshot(worker.model))
            break
        end
        if worker.model.paused
            isempty(queued) || _publish_latest!(worker, snapshot(worker.model))
            take!(worker.wake_signal)
            active_wall_started = time_ns()
            active_simulation_started = worker.model.simulation_time
            continue
        end
        started = time_ns()
        try
            guarded_trial = worker.model.pose_only_guarded_trial
            update!(worker.model)
            completed = time_ns()
            wall_delta = max((completed - active_wall_started) / 1.0e9, 1.0e-9)
            simulation_delta = worker.model.simulation_time - active_simulation_started
            instantaneous_throughput = simulation_delta / wall_delta
            smoothing = 0.15
            interactive_throughput = interactive_throughput === nothing ?
                instantaneous_throughput :
                (1 - smoothing) * interactive_throughput + smoothing * instantaneous_throughput
            worker.model.simulated_seconds_per_wall_second = interactive_throughput
            active_wall_started = completed
            active_simulation_started = worker.model.simulation_time
            _publish_latest!(worker, snapshot(worker.model))
            guarded_trial && (worker.model.pose_only_guarded_trial = false)
            worker.recovery_pending = false
        catch error
            if !(error isa NumericalFailure)
                worker.model.paused = true
                worker.model.status_message =
                    "worker error $(typeof(error)): " * sprint(showerror, error)
                _publish_latest!(worker, snapshot(worker.model))
                continue
            end
            failure_count = _record_failure!(worker, time_ns() / 1.0e9)
            reynolds_modified = reynolds(worker.model.solver) != worker.model.scenario.reynolds
            pose_only_recovery = worker.recovery_pending &&
                rapid_drag_attempted(worker.model) && !worker.model.pose_only_drag
            reset_reynolds = !pose_only_recovery && reynolds_modified &&
                (worker.recovery_pending || failure_count >= 3)
            baseline_circuit_break = !reynolds_modified && failure_count >= 3
            guarded_trial_failed = worker.model.pose_only_guarded_trial
            if guarded_trial_failed || baseline_circuit_break
                worker.model.paused = true
                worker.model.status_message =
                    "paused after repeated $(classify_viewer_failure(error))"
            elseif worker.recovery_pending && !reset_reynolds && !pose_only_recovery
                worker.model.paused = true
                worker.model.status_message =
                    "paused after repeated $(classify_viewer_failure(error))"
            else
                try
                    pose_only_recovery && enable_pose_only_drag!(worker.model)
                    recover_solver!(
                        worker.model,
                        error;
                        reset_reynolds,
                        post_import = worker.model.warm_validation_pending,
                    )
                    worker.recovery_pending = true
                    (reset_reynolds || pose_only_recovery) && _clear_failure_history!(worker)
                catch recovery_error
                    worker.model.paused = true
                    worker.model.status_message =
                        "$(classify_viewer_failure(error)); fresh restart failed: " *
                        sprint(showerror, recovery_error)
                end
            end
            _publish_latest!(worker, snapshot(worker.model))
        end
        remaining = 1 / 60 - (time_ns() - started) / 1.0e9
        remaining > 0 && timedwait(
            () -> isready(worker.wake_signal),
            remaining;
            pollint = min(0.001, remaining),
        )
    end
    return nothing
end

function start!(worker::ViewerWorker)
    worker.task === nothing || throw(ArgumentError("viewer worker is already started"))
    worker.accepting_commands || throw(ArgumentError("viewer worker is closed"))
    worker.task = Threads.@spawn _worker_loop(worker)
    return worker
end

function enqueue!(worker::ViewerWorker, command::SetAngleCommand)
    selected = lock(worker.command_lock) do
        worker.accepting_commands || throw(ArgumentError("viewer worker is closed"))
        queued = QueuedViewerCommand(worker.next_sequence, command)
        worker.next_sequence += 1
        worker.latest_angle[] = queued
        queued
    end
    _signal!(worker)
    return selected.sequence
end

function enqueue!(worker::ViewerWorker, command::ViewerCommand)
    selected = lock(worker.command_lock) do
        worker.accepting_commands || throw(ArgumentError("viewer worker is closed"))
        pending_pose = worker.latest_angle[]
        pending_pose === nothing || put!(worker.commands, pending_pose)
        worker.latest_angle[] = nothing
        queued = QueuedViewerCommand(worker.next_sequence, command)
        worker.next_sequence += 1
        put!(worker.commands, queued)
        queued
    end
    _signal!(worker)
    return selected.sequence
end

function _enqueue_stop!(worker::ViewerWorker)
    return lock(worker.command_lock) do
        worker.accepting_commands || return nothing
        pending_pose = worker.latest_angle[]
        pending_pose === nothing || put!(worker.commands, pending_pose)
        worker.latest_angle[] = nothing
        queued = QueuedViewerCommand(worker.next_sequence, StopViewerCommand())
        worker.next_sequence += 1
        put!(worker.commands, queued)
        worker.accepting_commands = false
        queued.sequence
    end
end

function latest_snapshot(worker::ViewerWorker)
    return lock(worker.snapshot_lock) do
        worker.latest[]
    end
end

function wait_for_revision(
    worker::ViewerWorker,
    revision::Integer;
    timeout::Float64 = 10.0,
)
    deadline = time() + timeout
    while time() < deadline
        selected = latest_snapshot(worker)
        selected !== nothing && selected.revision >= revision && return selected
        sleep(0.001)
    end
    error("timed out waiting for viewer revision $revision")
end

function wait_for_command(
    worker::ViewerWorker,
    sequence::Integer;
    timeout::Float64 = 10.0,
)
    deadline = time() + timeout
    while time() < deadline
        selected = latest_snapshot(worker)
        selected !== nothing && selected.applied_command >= sequence && return selected
        sleep(0.001)
    end
    error("timed out waiting for viewer command $sequence")
end

function close!(worker::ViewerWorker)
    if worker.task === nothing
        lock(worker.command_lock) do
            worker.accepting_commands = false
        end
        return nothing
    end
    sequence = _enqueue_stop!(worker)
    sequence === nothing || _signal!(worker)
    sequence === nothing || wait_for_command(worker, sequence)
    wait(worker.task)
    worker.task = nothing
    return nothing
end
