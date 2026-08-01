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
    foil::FoilSpec{T}
    controls::Vector{ControlKeyframe{T}}
    duration::T
    output_dt::T
    precision::Symbol
    seed::UInt64
    solver_options::Dict{String,Any}
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
    pivot = SVector{2,T}(document["foil"]["pivot"])
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
