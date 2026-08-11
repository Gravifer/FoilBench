abstract type ViewerCommand end
struct TogglePauseCommand <: ViewerCommand end
struct ToggleVorticityCommand <: ViewerCommand end
struct ToggleDiagnosticsCommand <: ViewerCommand end
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
    commands::Vector{QueuedViewerCommand}
    latest_angle::Base.RefValue{Union{Nothing,QueuedViewerCommand}}
    command_lock::ReentrantLock
    command_condition::Threads.Condition
    next_sequence::UInt64
    accepting_commands::Bool
    task::Union{Nothing,Task}
    running::Base.RefValue{Bool}
    snapshot_lock::ReentrantLock
    snapshot_condition::Threads.Condition
    latest::Base.RefValue{Union{Nothing,ViewerSnapshot{T}}}
    revision::UInt64
    applied_command::UInt64
    recovery_pending::Bool
    recent_failures::Vector{Float64}
end

function ViewerWorker(model::ViewerModel{T}) where {T}
    command_lock = ReentrantLock()
    snapshot_lock = ReentrantLock()
    return ViewerWorker(
        model,
        QueuedViewerCommand[],
        Ref{Union{Nothing,QueuedViewerCommand}}(nothing),
        command_lock,
        Threads.Condition(command_lock),
        UInt64(1),
        true,
        nothing,
        Ref(false),
        snapshot_lock,
        Threads.Condition(snapshot_lock),
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
        selected.solver_epoch,
        selected.solver_state_revision,
        selected.diagnostic_solver_state_revision,
        selected.vorticity_solver_state_revision,
        selected.time,
        selected.angle_degrees,
        selected.solver_id,
        selected.tracer_positions,
        selected.path_segments,
        selected.vorticity,
        selected.diagnostics,
        selected.status,
        selected.paused,
        selected.vorticity_visible,
        selected.crop_enabled,
        selected.tracer_mode,
        selected.phase,
        selected.motion_mode,
        selected.diagnostic_mode,
        selected.schedule_active,
        selected.recovery_epoch,
    )
end

function _publish_latest!(worker::ViewerWorker, selected::ViewerSnapshot)
    lock(worker.snapshot_lock) do
        worker.revision += 1
        worker.latest[] = _with_revision(selected, worker.revision, worker.applied_command)
        notify(worker.snapshot_condition; all = true)
    end
    return nothing
end

function _failed_snapshot(selected::ViewerSnapshot{T}, message::AbstractString) where {T}
    return ViewerSnapshot(
        selected.revision,
        selected.applied_command,
        selected.solver_epoch,
        selected.solver_state_revision,
        selected.diagnostic_solver_state_revision,
        selected.vorticity_solver_state_revision,
        selected.time,
        selected.angle_degrees,
        selected.solver_id,
        selected.tracer_positions,
        selected.path_segments,
        selected.vorticity,
        selected.diagnostics,
        selected.status * "  owner-error=" * String(message),
        true,
        selected.vorticity_visible,
        selected.crop_enabled,
        selected.tracer_mode,
        :failed,
        selected.motion_mode,
        selected.diagnostic_mode,
        selected.schedule_active,
        selected.recovery_epoch,
    )
end

function _publish_model!(worker::ViewerWorker)
    try
        _publish_latest!(worker, snapshot(worker.model))
    catch error
        worker.model.paused = true
        worker.model.status_message =
            "snapshot failure $(typeof(error)): " * sprint(showerror, error)
        previous = latest_snapshot(worker)
        previous === nothing && rethrow()
        _publish_latest!(worker, _failed_snapshot(previous, sprint(showerror, error)))
    end
    return nothing
end

function _apply_command!(worker::ViewerWorker, command::ViewerCommand)
    model = worker.model
    publish_boundary = false
    command isa TogglePauseCommand && toggle_pause!(model)
    command isa ToggleVorticityCommand && toggle_vorticity!(model)
    command isa ToggleDiagnosticsCommand && toggle_diagnostics!(model)
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
        publish_boundary = true
    end
    command isa SetAngleCommand && set_angle!(model, command.angle_degrees, command.timestamp)
    command isa ReleaseAngleCommand && release_angle!(model)
    if command isa AdjustReynoldsCommand
        adjust_reynolds!(model, command.decades)
        worker.recovery_pending = false
        _clear_failure_history!(worker)
    end
    if command isa SwitchSolverCommand
        source_solver_id = solver_info(model.solver).id
        outcome = switch_solver!(model, command.solver_id)
        destination_changed = accepted(outcome) && solver_info(model.solver).id != source_solver_id
        if destination_changed
            worker.recovery_pending = false
            _clear_failure_history!(worker)
        end
        publish_boundary = true
    end
    command isa AdjustTuningCommand && adjust_tuning!(model, command.amount)
    command isa StopViewerCommand && (worker.running[] = false)
    return publish_boundary
end

function _drain_commands!(worker::ViewerWorker)
    return lock(worker.command_lock) do
        queued = copy(worker.commands)
        empty!(worker.commands)
        selected = worker.latest_angle[]
        worker.latest_angle[] = nothing
        selected === nothing || push!(queued, selected)
        sort!(queued; by = command -> command.sequence)
        queued
    end
end

function _wait_for_commands!(worker::ViewerWorker, timeout::Union{Nothing,Float64} = nothing)
    timer = nothing
    lock(worker.command_lock)
    try
        (!isempty(worker.commands) || worker.latest_angle[] !== nothing || !worker.running[]) &&
            return nothing
        if timeout !== nothing
            timeout <= 0 && return nothing
            timer = Timer(timeout) do _
                lock(worker.command_lock) do
                    notify(worker.command_condition; all = true)
                end
            end
        end
        wait(worker.command_condition)
    finally
        timer === nothing || close(timer)
        unlock(worker.command_lock)
    end
    return nothing
end

function _worker_loop(worker::ViewerWorker)
    worker.running[] = true
    _publish_model!(worker)
    active_wall_started = Int(time_ns())
    active_simulation_started = worker.model.simulation_time
    interactive_throughput = nothing
    step_interval_ns = round(Int, 1.0e9 / 60)
    next_step_deadline = active_wall_started + step_interval_ns
    while worker.running[]
        queued = _drain_commands!(worker)
        publish_boundary = false
        for selected in queued
            try
                publish_boundary =
                    _apply_command!(worker, selected.command) || publish_boundary
            catch error
                worker.model.paused = true
                worker.model.status_message =
                    "worker command error $(typeof(error)): " * sprint(showerror, error)
            end
            worker.applied_command = max(worker.applied_command, selected.sequence)
        end
        if !worker.running[]
            _publish_model!(worker)
            break
        end
        if worker.model.paused
            isempty(queued) || _publish_model!(worker)
            _wait_for_commands!(worker)
            active_wall_started = Int(time_ns())
            active_simulation_started = worker.model.simulation_time
            next_step_deadline = active_wall_started
            continue
        end
        if publish_boundary
            _publish_model!(worker)
            active_wall_started = Int(time_ns())
            active_simulation_started = worker.model.simulation_time
            interactive_throughput = nothing
            next_step_deadline = active_wall_started + step_interval_ns
            continue
        end
        remaining_before_step = (next_step_deadline - Int(time_ns())) / 1.0e9
        if remaining_before_step > 0
            _wait_for_commands!(worker, remaining_before_step)
            continue
        end
        started = Int(time_ns())
        next_step_deadline = started + step_interval_ns
        try
            guarded_trial = worker.model.pose_only_guarded_trial
            update!(worker.model)
            completed = Int(time_ns())
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
            _publish_model!(worker)
            guarded_trial && (worker.model.pose_only_guarded_trial = false)
            worker.recovery_pending = false
        catch error
            if !(error isa NumericalFailure)
                worker.model.paused = true
                worker.model.status_message =
                    "worker error $(typeof(error)): " * sprint(showerror, error)
                _publish_model!(worker)
                continue
            end
            failure_count = _record_failure!(worker, Int(time_ns()) / 1.0e9)
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
            _publish_model!(worker)
        end
        remaining = 1 / 60 - (Int(time_ns()) - started) / 1.0e9
        remaining > 0 && _wait_for_commands!(worker, remaining)
    end
    return nothing
end

function _supervised_worker_loop(worker::ViewerWorker)
    try
        _worker_loop(worker)
    catch error
        worker.model.paused = true
        worker.model.status_message =
            "owner failure $(typeof(error)): " * sprint(showerror, error)
        try
            _publish_model!(worker)
        catch
        end
        rethrow()
    finally
        worker.running[] = false
        lock(worker.command_lock) do
            worker.accepting_commands = false
            notify(worker.command_condition; all = true)
        end
        lock(worker.snapshot_lock) do
            notify(worker.snapshot_condition; all = true)
        end
    end
    return nothing
end

function start!(worker::ViewerWorker)
    worker.task === nothing || throw(ArgumentError("viewer worker is already started"))
    worker.accepting_commands || throw(ArgumentError("viewer worker is closed"))
    worker.task = errormonitor(Threads.@spawn _supervised_worker_loop(worker))
    return worker
end

function enqueue!(worker::ViewerWorker, command::SetAngleCommand)
    selected = lock(worker.command_lock) do
        worker.accepting_commands || throw(ArgumentError("viewer worker is closed"))
        queued = QueuedViewerCommand(worker.next_sequence, command)
        worker.next_sequence += 1
        worker.latest_angle[] = queued
        notify(worker.command_condition; all = true)
        queued
    end
    return selected.sequence
end

function enqueue!(worker::ViewerWorker, command::ViewerCommand)
    selected = lock(worker.command_lock) do
        worker.accepting_commands || throw(ArgumentError("viewer worker is closed"))
        pending_pose = worker.latest_angle[]
        pending_pose === nothing || push!(worker.commands, pending_pose)
        worker.latest_angle[] = nothing
        queued = QueuedViewerCommand(worker.next_sequence, command)
        worker.next_sequence += 1
        push!(worker.commands, queued)
        notify(worker.command_condition; all = true)
        queued
    end
    return selected.sequence
end

function _enqueue_stop!(worker::ViewerWorker)
    return lock(worker.command_lock) do
        worker.accepting_commands || return nothing
        pending_pose = worker.latest_angle[]
        pending_pose === nothing || push!(worker.commands, pending_pose)
        worker.latest_angle[] = nothing
        queued = QueuedViewerCommand(worker.next_sequence, StopViewerCommand())
        worker.next_sequence += 1
        push!(worker.commands, queued)
        worker.accepting_commands = false
        notify(worker.command_condition; all = true)
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
    timer = nothing
    lock(worker.snapshot_lock)
    try
        timer = Timer(timeout) do _
            lock(worker.snapshot_lock) do
                notify(worker.snapshot_condition; all = true)
            end
        end
        while true
            selected = worker.latest[]
            selected !== nothing && selected.revision >= revision && return selected
            time() >= deadline && error("timed out waiting for viewer revision $revision")
            wait(worker.snapshot_condition)
        end
    finally
        timer === nothing || close(timer)
        unlock(worker.snapshot_lock)
    end
end

function wait_for_command(
    worker::ViewerWorker,
    sequence::Integer;
    timeout::Float64 = 10.0,
)
    deadline = time() + timeout
    timer = nothing
    lock(worker.snapshot_lock)
    try
        timer = Timer(timeout) do _
            lock(worker.snapshot_lock) do
                notify(worker.snapshot_condition; all = true)
            end
        end
        while true
            selected = worker.latest[]
            selected !== nothing && selected.applied_command >= sequence && return selected
            time() >= deadline && error("timed out waiting for viewer command $sequence")
            wait(worker.snapshot_condition)
        end
    finally
        timer === nothing || close(timer)
        unlock(worker.snapshot_lock)
    end
end

function close!(worker::ViewerWorker)
    if worker.task === nothing
        lock(worker.command_lock) do
            worker.accepting_commands = false
        end
        return nothing
    end
    sequence = _enqueue_stop!(worker)
    sequence === nothing || wait_for_command(worker, sequence)
    wait(worker.task)
    worker.task = nothing
    return nothing
end
