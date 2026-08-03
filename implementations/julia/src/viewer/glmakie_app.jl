module FoilBenchGLMakie

using FoilBenchJulia
using GLMakie

function _points(values::AbstractMatrix)
    return [Point2f(values[1, index], values[2, index]) for index in axes(values, 2)]
end

function _view_bounds(scenario::Scenario{2,T}, cropped::Bool) where {T}
    crop_cells = cropped ? option(scenario, "viewer_crop_cells", 0) : 0
    x0, x1 = scenario.domain.bounds[1]
    y0, y1 = scenario.domain.bounds[2]
    return (
        (x0 + T(crop_cells) * dx(scenario.domain), x1 - T(crop_cells) * dx(scenario.domain)),
        (y0 + T(crop_cells) * dy(scenario.domain), y1 - T(crop_cells) * dy(scenario.domain)),
    )
end

function _apply_limits!(axis, scenario, cropped::Bool)
    x_bounds, y_bounds = _view_bounds(scenario, cropped)
    xlims!(axis, x_bounds...)
    ylims!(axis, y_bounds...)
    return nothing
end

function _reserve_left_drag!(axis)
    deactivate_interaction!(axis, :rectanglezoom)
    return axis
end

function _vorticity_colormap()
    return [
        begin
            magnitude = min(abs(value), 1)^0.7
            ramp = clamp((magnitude - 0.18) / (0.9 - 0.18), 0, 1)
            visibility = ramp * ramp * (3 - 2 * ramp)
            red, green, blue = value >= 0 ? (0.65, 0.12, 0.02) : (0.02, 0.28, 0.65)
            RGBAf(red, green, blue, 0.38 * visibility)
        end
        for value in range(-1, 1; length = 257)
    ]
end

function run_viewer(
    scenario_path::AbstractString;
    solver_id::AbstractString = "stable-fluids",
)
    scenario = load_scenario(scenario_path)
    dimension(scenario) == 2 || throw(ArgumentError("the Julia viewer currently supports 2D"))
    model = ViewerModel(scenario; solver_id)
    worker = start!(ViewerWorker(model))
    initial = nothing
    while initial === nothing
        initial = latest_snapshot(worker)
        initial === nothing && sleep(0.01)
    end

    GLMakie.activate!(; title = "FoilBench Julia", framerate = 60.0, vsync = true)
    figure = Figure(; size = (1280, 760), backgroundcolor = :black)
    axis = Axis(
        figure[1, 1];
        backgroundcolor = RGBf(0.015, 0.02, 0.035),
        xgridvisible = false,
        ygridvisible = false,
    )
    hidedecorations!(axis)
    _reserve_left_drag!(axis)
    _apply_limits!(axis, scenario, initial.crop_enabled)

    x_centers = range(
        scenario.domain.bounds[1][1] + 0.5 * dx(scenario.domain),
        scenario.domain.bounds[1][2] - 0.5 * dx(scenario.domain);
        length = nx(scenario.domain),
    )
    y_centers = range(
        scenario.domain.bounds[2][1] + 0.5 * dy(scenario.domain),
        scenario.domain.bounds[2][2] - 0.5 * dy(scenario.domain);
        length = ny(scenario.domain),
    )
    vorticity_data = Observable(initial.vorticity)
    vorticity_visible = Observable(initial.vorticity_visible)
    heatmap!(
        axis,
        x_centers,
        y_centers,
        vorticity_data;
        colormap = _vorticity_colormap(),
        colorrange = (-1, 1),
        interpolate = true,
        visible = vorticity_visible,
    )
    path_data = Observable(_points(initial.path_segments))
    tracer_data = Observable(_points(initial.tracer_positions))
    foil_data = Observable(_points(foil_outline(model.geometry, initial.angle_degrees)))
    linesegments!(axis, path_data; color = RGBAf(0.05, 0.55, 1.0, 0.32), linewidth = 1)
    scatter!(axis, tracer_data; color = RGBf(0.1, 0.65, 1.0), markersize = 2)
    lines!(axis, foil_data; color = RGBf(0.82, 0.88, 0.94), linewidth = 1.5)
    status_text = Observable(initial.status)
    Label(
        figure[2, 1],
        status_text;
        color = :white,
        fontsize = 12,
        tellwidth = false,
        halign = :left,
    )
    Label(
        figure[3, 1],
        "1/2/3 solver   left-drag foil   Space pause   R reset   -/+ Re   " *
        "0 Re reset   [/] solver tuning   V vorticity   D diagnostics   T tracers   C crop";
        color = RGBf(0.72, 0.78, 0.88),
        fontsize = 10,
        tellwidth = false,
        halign = :left,
    )

    last_crop = Ref(initial.crop_enabled)
    last_revision = Ref(initial.revision)
    function apply_snapshot!(selected)
        tracer_data[] = _points(selected.tracer_positions)
        path_data[] = _points(selected.path_segments)
        foil_data[] = _points(foil_outline(model.geometry, selected.angle_degrees))
        if selected.vorticity_visible && !isempty(selected.vorticity)
            vorticity_data[] = selected.vorticity
        end
        vorticity_visible[] = selected.vorticity_visible
        status_text[] = selected.status
        if selected.crop_enabled != last_crop[]
            _apply_limits!(axis, scenario, selected.crop_enabled)
            last_crop[] = selected.crop_enabled
        end
        return nothing
    end

    on(events(figure).tick) do _
        selected = latest_snapshot(worker)
        if selected !== nothing && selected.revision != last_revision[]
            apply_snapshot!(selected)
            last_revision[] = selected.revision
        end
        return Consume(false)
    end

    T = scalar_type(scenario)
    on(events(figure).unicode_input) do character
        command = if character == ' '
            TogglePauseCommand()
        elseif lowercase(character) == 'r'
            ResetViewerCommand()
        elseif character == '1'
            SwitchSolverCommand("stable-fluids")
        elseif character == '2'
            SwitchSolverCommand("lbm-d2q9")
        elseif character == '3'
            SwitchSolverCommand("pic-flip")
        elseif character == '['
            AdjustTuningCommand(T(-0.05))
        elseif character == ']'
            AdjustTuningCommand(T(0.05))
        elseif character == '-'
            AdjustReynoldsCommand(T(-0.25))
        elseif character in ('+', '=')
            AdjustReynoldsCommand(T(0.25))
        elseif character == '0'
            ResetReynoldsCommand()
        elseif lowercase(character) == 'v'
            ToggleVorticityCommand()
        elseif lowercase(character) == 'd'
            ToggleDiagnosticsCommand()
        elseif lowercase(character) == 't'
            ToggleTracerCommand()
        elseif lowercase(character) == 'c'
            ToggleCropCommand()
        else
            nothing
        end
        command === nothing || enqueue!(worker, command)
        return Consume(command !== nothing)
    end

    dragging = Ref(false)
    on(events(figure).mousebutton, priority = 2) do event
        if event.button == Mouse.left && event.action == Mouse.press
            dragging[] = true
            return Consume(true)
        elseif event.button == Mouse.left && event.action == Mouse.release
            dragging[] = false
            enqueue!(worker, ReleaseAngleCommand())
            return Consume(true)
        end
        return Consume(false)
    end
    on(events(figure).mouseposition, priority = 2) do _
        dragging[] || return Consume(false)
        world_x, world_y = mouseposition(axis)
        pivot = scenario.foil.pivot
        angle = rad2deg(atan(world_y - pivot[2], world_x - pivot[1]))
        timestamp = time_ns() / 1.0e9
        enqueue!(worker, SetAngleCommand(T(angle), timestamp))
        return Consume(true)
    end

    screen = GLMakie.Screen()
    try
        display(screen, figure)
        wait(screen)
    finally
        close!(worker)
    end
    return nothing
end

end
