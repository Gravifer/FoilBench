function faces_to_cell(u::AbstractMatrix{T}, v::AbstractMatrix{T}) where {T<:AbstractFloat}
    nx_value = size(u, 1) - 1
    ny_value = size(v, 2) - 1
    size(u, 2) == ny_value || throw(DimensionMismatch("incompatible MAC face arrays"))
    size(v, 1) == nx_value || throw(DimensionMismatch("incompatible MAC face arrays"))
    velocity = Array{T,3}(undef, nx_value, ny_value, 2)
    for j in 1:ny_value, i in 1:nx_value
        velocity[i, j, 1] = T(0.5) * (u[i, j] + u[i + 1, j])
        velocity[i, j, 2] = T(0.5) * (v[i, j] + v[i, j + 1])
    end
    return velocity
end

function cell_to_faces(velocity::AbstractArray{T,3}) where {T<:AbstractFloat}
    nx_value, ny_value, components = size(velocity)
    components == 2 || throw(DimensionMismatch("cell velocity must have two components"))
    u = Matrix{T}(undef, nx_value + 1, ny_value)
    v = Matrix{T}(undef, nx_value, ny_value + 1)
    for j in 1:ny_value
        u[1, j] = velocity[1, j, 1]
        u[end, j] = velocity[end, j, 1]
        for i in 2:nx_value
            u[i, j] = T(0.5) * (velocity[i - 1, j, 1] + velocity[i, j, 1])
        end
    end
    for i in 1:nx_value
        v[i, 1] = velocity[i, 1, 2]
        v[i, end] = velocity[i, end, 2]
        for j in 2:ny_value
            v[i, j] = T(0.5) * (velocity[i, j - 1, 2] + velocity[i, j, 2])
        end
    end
    return u, v
end

function apply_domain_boundaries!(
    u::AbstractMatrix{T},
    v::AbstractMatrix{T},
    domain::DomainSpec{2,T},
    freestream::SVector{2,T};
    channel_walls::Bool = false,
) where {T<:AbstractFloat}
    if :x in domain.periodic_axes
        periodic_u = T(0.5) .* (u[1, :] .+ u[end, :])
        u[1, :] .= periodic_u
        u[end, :] .= periodic_u
    else
        u[1, :] .= freestream[1]
        u[end, :] .= u[end - 1, :]
        v[1, :] .= freestream[2]
        v[end, :] .= v[end - 1, :]
    end
    if :y in domain.periodic_axes
        periodic_v = T(0.5) .* (v[:, 1] .+ v[:, end])
        v[:, 1] .= periodic_v
        v[:, end] .= periodic_v
    elseif channel_walls
        v[:, 1] .= zero(T)
        v[:, end] .= zero(T)
        u[:, 1] .= zero(T)
        u[:, end] .= zero(T)
    else
        v[:, 1] .= freestream[2]
        v[:, end] .= freestream[2]
        u[:, 1] .= freestream[1]
        u[:, end] .= freestream[1]
    end
    return nothing
end

function enforce_solid_faces!(
    u::AbstractMatrix{T},
    v::AbstractMatrix{T},
    solid::AbstractMatrix{Bool},
    wall_velocity::AbstractArray{T,3},
) where {T<:AbstractFloat}
    nx_value, ny_value = size(solid)
    wall_u, wall_v = cell_to_faces(wall_velocity)
    for j in 1:ny_value, i in 1:(nx_value + 1)
        left_solid = i > 1 && solid[i - 1, j]
        right_solid = i <= nx_value && solid[i, j]
        (left_solid || right_solid) && (u[i, j] = wall_u[i, j])
    end
    for j in 1:(ny_value + 1), i in 1:nx_value
        lower_solid = j > 1 && solid[i, j - 1]
        upper_solid = j <= ny_value && solid[i, j]
        (lower_solid || upper_solid) && (v[i, j] = wall_v[i, j])
    end
    return nothing
end

function face_divergence(
    u::AbstractMatrix{T},
    v::AbstractMatrix{T},
    domain::DomainSpec{2,T},
) where {T<:AbstractFloat}
    output = Matrix{T}(undef, nx(domain), ny(domain))
    for j in 1:ny(domain), i in 1:nx(domain)
        output[i, j] =
            (u[i + 1, j] - u[i, j]) / dx(domain) +
            (v[i, j + 1] - v[i, j]) / dy(domain)
    end
    return output
end

function cell_to_canonical(velocity::AbstractArray{T,3}) where {T<:AbstractFloat}
    nx_value, ny_value, dimensions = size(velocity)
    dimensions == 2 || throw(DimensionMismatch("Phase 2A canonical conversion expects 2D"))
    canonical = Array{T,4}(undef, 1, ny_value, nx_value, 2)
    for component in 1:2, j in 1:ny_value, i in 1:nx_value
        canonical[1, j, i, component] = velocity[i, j, component]
    end
    return canonical
end

function canonical_to_cell(state::CanonicalFlowState{2,T}) where {T<:AbstractFloat}
    velocity = Array{T,3}(undef, state.resolution[1], state.resolution[2], 2)
    for component in 1:2, j in axes(velocity, 2), i in axes(velocity, 1)
        velocity[i, j, component] = state.velocity[1, j, i, component]
    end
    return velocity
end
