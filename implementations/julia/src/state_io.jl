struct CanonicalFlowState{D,T<:AbstractFloat}
    schema_version::Int
    bounds::NTuple{D,NTuple{2,T}}
    resolution::NTuple{D,Int}
    periodic_axes::Tuple{Vararg{Symbol}}
    time::T
    angle_degrees::T
    angular_velocity_degrees::T
    source_language::String
    source_solver::String
    velocity::Array{T,4}
    density::Union{Nothing,Array{T,3}}

    function CanonicalFlowState(
        schema_version::Int,
        bounds::NTuple{D,NTuple{2,T}},
        resolution::NTuple{D,Int},
        periodic_axes::Tuple{Vararg{Symbol}},
        time::T,
        angle_degrees::T,
        angular_velocity_degrees::T,
        source_language::AbstractString,
        source_solver::AbstractString,
        velocity::Array{T,4},
        density::Union{Nothing,Array{T,3}} = nothing,
    ) where {D,T<:AbstractFloat}
        D in (2, 3) || throw(ArgumentError("canonical dimension must be two or three"))
        expected_z = D == 2 ? 1 : resolution[3]
        expected_velocity = (expected_z, resolution[2], resolution[1], D)
        size(velocity) == expected_velocity ||
            throw(DimensionMismatch("canonical velocity shape does not match metadata"))
        density === nothing || size(density) == expected_velocity[1:3] ||
            throw(DimensionMismatch("canonical density shape does not match metadata"))
        all(isfinite, velocity) || throw(ArgumentError("canonical velocity must be finite"))
        density === nothing || all(isfinite, density) ||
            throw(ArgumentError("canonical density must be finite"))
        return new{D,T}(
            schema_version,
            bounds,
            resolution,
            periodic_axes,
            time,
            angle_degrees,
            angular_velocity_degrees,
            String(source_language),
            String(source_solver),
            velocity,
            density,
        )
    end
end

dimension(::CanonicalFlowState{D}) where {D} = D
scalar_type(::CanonicalFlowState{D,T}) where {D,T} = T

function _validate_array_metadata(
    manifest::Dict{String,Any},
    name::String,
    expected_axes::Vector{String},
)
    metadata = manifest[name]
    metadata === nothing && return nothing
    metadata["file"] == "$name.npy" || throw(ArgumentError("invalid canonical $name file"))
    String.(metadata["axes"]) == expected_axes || throw(ArgumentError("invalid canonical $name axes"))
    metadata["order"] in ("C", "F") ||
        throw(ArgumentError("canonical arrays must declare C or Fortran order"))
    return metadata
end

function _load_canonical_state(manifest::Dict{String,Any}, directory::AbstractString, ::Val{D}) where {D}
    _validate_array_metadata(manifest, "velocity", ["z", "y", "x", "component"])
    density_metadata = _validate_array_metadata(manifest, "density", ["z", "y", "x"])
    precision_name = String(manifest["precision"])
    T = precision_name == "float32" ? Float32 : precision_name == "float64" ? Float64 :
        throw(ArgumentError("unsupported canonical precision"))
    bounds = ntuple(index -> (T(manifest["bounds"][index][1]), T(manifest["bounds"][index][2])), D)
    resolution = ntuple(index -> Int(manifest["resolution"][index]), D)
    periodic_axes = Tuple(Symbol(value) for value in manifest["periodic_axes"])
    velocity = Array{T,4}(npzread(joinpath(directory, "velocity.npy")))
    density = density_metadata === nothing ? nothing :
        Array{T,3}(npzread(joinpath(directory, "density.npy")))
    return CanonicalFlowState(
        Int(manifest["schema_version"]),
        bounds,
        resolution,
        periodic_axes,
        T(manifest["time"]),
        T(manifest["angle_degrees"]),
        T(manifest["angular_velocity_degrees"]),
        String(manifest["source_language"]),
        String(manifest["source_solver"]),
        velocity,
        density,
    )
end

function load_canonical_state(directory::AbstractString)
    manifest = JSON3.read(read(joinpath(directory, "manifest.json"), String), Dict{String,Any})
    schema_path = normpath(
        joinpath(@__DIR__, "..", "..", "..", "spec", "canonical-manifest.schema.json"),
    )
    validate_json_file(manifest, schema_path)
    dimension_value = Int(manifest["dimension"])
    dimension_value in (2, 3) || throw(ArgumentError("canonical dimension must be two or three"))
    return _load_canonical_state(manifest, directory, Val(dimension_value))
end

function save_canonical_state(state::CanonicalFlowState{D,T}, directory::AbstractString) where {D,T}
    mkpath(directory)
    npzwrite(joinpath(directory, "velocity.npy"), state.velocity)
    state.density === nothing || npzwrite(joinpath(directory, "density.npy"), state.density)
    manifest = Dict{String,Any}(
        "schema_version" => state.schema_version,
        "dimension" => D,
        "bounds" => [collect(pair) for pair in state.bounds],
        "resolution" => collect(state.resolution),
        "periodic_axes" => String.(state.periodic_axes),
        "time" => state.time,
        "precision" => T === Float32 ? "float32" : "float64",
        "angle_degrees" => state.angle_degrees,
        "angular_velocity_degrees" => state.angular_velocity_degrees,
        "source_language" => state.source_language,
        "source_solver" => state.source_solver,
        "velocity" => Dict(
            "file" => "velocity.npy",
            "axes" => ["z", "y", "x", "component"],
            "order" => "F",
        ),
        "density" => state.density === nothing ? nothing : Dict(
            "file" => "density.npy",
            "axes" => ["z", "y", "x"],
            "order" => "F",
        ),
    )
    open(joinpath(directory, "manifest.json"), "w") do io
        JSON3.pretty(io, manifest)
    end
    return directory
end
