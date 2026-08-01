struct CanonicalFlowState{T<:AbstractFloat}
    manifest::Dict{String,Any}
    velocity::Array{T,4}
    density::Union{Nothing,Array{T,3}}
end

function _validate_array_metadata(
    manifest::Dict{String,Any},
    name::String,
    expected_axes::Vector{String},
)
    metadata = manifest[name]
    metadata === nothing && return nothing
    metadata["file"] == "$name.npy" || throw(ArgumentError("invalid canonical $name file"))
    String.(metadata["axes"]) == expected_axes || throw(ArgumentError("invalid canonical $name axes"))
    metadata["order"] == "C" || throw(ArgumentError("canonical arrays must declare C order"))
    return metadata
end

function load_canonical_state(directory::AbstractString)
    manifest = JSON3.read(read(joinpath(directory, "manifest.json"), String), Dict{String,Any})
    _validate_array_metadata(manifest, "velocity", ["z", "y", "x", "component"])
    density_metadata = _validate_array_metadata(manifest, "density", ["z", "y", "x"])
    precision = manifest["precision"]
    T = precision == "float32" ? Float32 : precision == "float64" ? Float64 :
        throw(ArgumentError("unsupported canonical precision"))
    velocity = Array{T,4}(npzread(joinpath(directory, "velocity.npy")))
    density = density_metadata === nothing ? nothing :
        Array{T,3}(npzread(joinpath(directory, "density.npy")))
    resolution = Int.(manifest["resolution"])
    dimension = Int(manifest["dimension"])
    expected_z = dimension == 2 ? 1 : resolution[3]
    size(velocity) == (expected_z, resolution[2], resolution[1], dimension) ||
        throw(DimensionMismatch("canonical velocity shape does not match manifest"))
    density === nothing || size(density) == (expected_z, resolution[2], resolution[1]) ||
        throw(DimensionMismatch("canonical density shape does not match manifest"))
    all(isfinite, velocity) || throw(ArgumentError("canonical velocity must be finite"))
    density === nothing || all(isfinite, density) ||
        throw(ArgumentError("canonical density must be finite"))
    return CanonicalFlowState{T}(manifest, velocity, density)
end
