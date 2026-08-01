struct DomainSpec{D,T<:AbstractFloat}
    bounds::NTuple{D,NTuple{2,T}}
    resolution::NTuple{D,Int}
    periodic_axes::Tuple{Vararg{Symbol}}

    function DomainSpec(
        bounds::NTuple{D,NTuple{2,T}},
        resolution::NTuple{D,Int},
        periodic_axes::Tuple{Vararg{Symbol}} = (),
    ) where {D,T<:AbstractFloat}
        all(pair -> pair[2] > pair[1], bounds) || throw(ArgumentError("invalid domain bounds"))
        all(size -> size >= 4, resolution) || throw(ArgumentError("resolution must be at least four"))
        return new{D,T}(bounds, resolution, periodic_axes)
    end
end

struct ControlKeyframe{T<:AbstractFloat}
    time::T
    angle_degrees::T
end

struct Scenario{D,T<:AbstractFloat}
    schema_version::Int
    id::String
    domain::DomainSpec{D,T}
    reynolds::T
    freestream::SVector{D,T}
    foil::FoilSpec{D,T}
    controls::Vector{ControlKeyframe{T}}
    duration::T
    output_dt::T
    precision::Symbol
    seed::UInt64
    solver_options::Dict{String,Any}
end

dimension(::DomainSpec{D}) where {D} = D
dimension(::Scenario{D}) where {D} = D
scalar_type(::Scenario{D,T}) where {D,T} = T

function control_at(scenario::Scenario{D,T}, time::Real) where {D,T}
    selected_time = T(time)
    controls = scenario.controls
    if length(controls) == 1 || selected_time <= first(controls).time
        return ControlState(selected_time, first(controls).angle_degrees, zero(T))
    end
    if selected_time >= last(controls).time
        return ControlState(selected_time, last(controls).angle_degrees, zero(T))
    end
    for index in 1:(length(controls) - 1)
        left = controls[index]
        right = controls[index + 1]
        left.time <= selected_time <= right.time || continue
        duration = right.time - left.time
        duration > zero(T) || return ControlState(selected_time, right.angle_degrees, zero(T))
        linear = (selected_time - left.time) / duration
        smooth = linear^2 * (T(3) - T(2) * linear)
        delta = right.angle_degrees - left.angle_degrees
        angle = left.angle_degrees + smooth * delta
        angular_velocity = T(6) * linear * (one(T) - linear) * delta / duration
        return ControlState(selected_time, angle, angular_velocity)
    end
    return ControlState(selected_time, last(controls).angle_degrees, zero(T))
end

function _load_scenario(document::Dict{String,Any}, ::Val{D}) where {D}
    T = document["precision"] == "float32" ? Float32 : Float64
    bounds = ntuple(
        index -> (T(document["bounds"][index][1]), T(document["bounds"][index][2])),
        D,
    )
    resolution = ntuple(index -> Int(document["resolution"][index]), D)
    axes = Tuple(Symbol(value) for value in document["periodic_axes"])
    domain = DomainSpec(bounds, resolution, axes)
    pivot = SVector{D,T}(document["foil"]["pivot"])
    foil = FoilSpec(document["foil"]["naca"], T(document["foil"]["chord"]), pivot)
    controls = [
        ControlKeyframe(T(value["time"]), T(value["angle_degrees"]))
        for value in document["controls"]
    ]
    return Scenario(
        Int(document["schema_version"]),
        String(document["id"]),
        domain,
        T(document["reynolds"]),
        SVector{D,T}(document["freestream"]),
        foil,
        controls,
        T(document["duration"]),
        T(document["output_dt"]),
        Symbol(document["precision"]),
        UInt64(document["seed"]),
        Dict{String,Any}(get(document, "solver_options", Dict{String,Any}())),
    )
end

function load_scenario(path::AbstractString)
    document = JSON3.read(read(path, String), Dict{String,Any})
    dimension = Int(document["dimension"])
    dimension in (2, 3) || throw(ArgumentError("scenario dimension must be two or three"))
    return _load_scenario(document, Val(dimension))
end
