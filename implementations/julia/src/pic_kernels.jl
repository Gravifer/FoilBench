@inline function quadratic_bspline_weight(distance::T) where {T<:AbstractFloat}
    absolute = abs(distance)
    absolute < T(0.5) && return T(0.75) - absolute^2
    if absolute < T(1.5)
        difference = T(1.5) - absolute
        return T(0.5) * difference^2
    end
    return zero(T)
end

@inline function _pic_transfer_index(source::Int, count::Int, periodic::Bool)
    return periodic ? mod1(source, count) : clamp(source, 1, count)
end

"""Gather an x-major cell field to `2 × particle` positions with quadratic B-splines."""
function grid_to_particle(
    grid::AbstractArray{T,3},
    positions::AbstractMatrix{T},
    domain::DomainSpec{2,T},
) where {T<:AbstractFloat}
    size(grid) == (nx(domain), ny(domain), 2) ||
        throw(DimensionMismatch("PIC grid does not match the domain"))
    size(positions, 1) == 2 || throw(DimensionMismatch("positions must have shape 2 × particle"))
    velocities = Matrix{T}(undef, 2, size(positions, 2))
    x0 = domain.bounds[1][1]
    y0 = domain.bounds[2][1]
    periodic_x = :x in domain.periodic_axes
    periodic_y = :y in domain.periodic_axes
    for particle in axes(positions, 2)
        gx = (positions[1, particle] - x0) / dx(domain) - T(0.5)
        gy = (positions[2, particle] - y0) / dy(domain) - T(0.5)
        base_x = floor(Int, gx - T(0.5))
        base_y = floor(Int, gy - T(0.5))
        velocity_x = zero(T)
        velocity_y = zero(T)
        weight_sum = zero(T)
        for offset_y in 0:2
            source_y_zero = base_y + offset_y
            target_y = _pic_transfer_index(source_y_zero + 1, ny(domain), periodic_y)
            weight_y = quadratic_bspline_weight(gy - T(source_y_zero))
            for offset_x in 0:2
                source_x_zero = base_x + offset_x
                target_x = _pic_transfer_index(source_x_zero + 1, nx(domain), periodic_x)
                weight = weight_y * quadratic_bspline_weight(gx - T(source_x_zero))
                velocity_x += weight * grid[target_x, target_y, 1]
                velocity_y += weight * grid[target_x, target_y, 2]
                weight_sum += weight
            end
        end
        inverse_weight = inv(max(weight_sum, T(1.0e-12)))
        velocities[1, particle] = velocity_x * inverse_weight
        velocities[2, particle] = velocity_y * inverse_weight
    end
    return velocities
end

"""Scatter `2 × particle` velocities to an x-major cell field deterministically."""
function particle_to_grid(
    positions::AbstractMatrix{T},
    velocities::AbstractMatrix{T},
    domain::DomainSpec{2,T},
    freestream::SVector{2,T},
) where {T<:AbstractFloat}
    size(positions, 1) == 2 || throw(DimensionMismatch("positions must have shape 2 × particle"))
    size(velocities) == size(positions) ||
        throw(DimensionMismatch("particle velocities must match positions"))
    weights = zeros(T, nx(domain), ny(domain))
    momentum = zeros(T, nx(domain), ny(domain), 2)
    x0 = domain.bounds[1][1]
    y0 = domain.bounds[2][1]
    periodic_x = :x in domain.periodic_axes
    periodic_y = :y in domain.periodic_axes
    for particle in axes(positions, 2)
        gx = (positions[1, particle] - x0) / dx(domain) - T(0.5)
        gy = (positions[2, particle] - y0) / dy(domain) - T(0.5)
        base_x = floor(Int, gx - T(0.5))
        base_y = floor(Int, gy - T(0.5))
        for offset_y in 0:2
            source_y_zero = base_y + offset_y
            target_y = _pic_transfer_index(source_y_zero + 1, ny(domain), periodic_y)
            weight_y = quadratic_bspline_weight(gy - T(source_y_zero))
            for offset_x in 0:2
                source_x_zero = base_x + offset_x
                target_x = _pic_transfer_index(source_x_zero + 1, nx(domain), periodic_x)
                weight = weight_y * quadratic_bspline_weight(gx - T(source_x_zero))
                weights[target_x, target_y] += weight
                momentum[target_x, target_y, 1] += weight * velocities[1, particle]
                momentum[target_x, target_y, 2] += weight * velocities[2, particle]
            end
        end
    end
    grid = Array{T,3}(undef, nx(domain), ny(domain), 2)
    for j in 1:ny(domain), i in 1:nx(domain)
        weight = weights[i, j]
        if weight > T(1.0e-12)
            grid[i, j, 1] = momentum[i, j, 1] / weight
            grid[i, j, 2] = momentum[i, j, 2] / weight
        else
            grid[i, j, 1] = freestream[1]
            grid[i, j, 2] = freestream[2]
        end
    end
    return grid
end

function _scatter_face_component(
    positions::AbstractMatrix{T},
    values::AbstractVector{T},
    domain::DomainSpec{2,T},
    width::Int,
    height::Int,
    gx_offset::T,
    gy_offset::T,
    duplicate_x::Bool,
    duplicate_y::Bool,
    fallback::AbstractMatrix{T},
) where {T<:AbstractFloat}
    size(fallback) == (width, height) || throw(DimensionMismatch("invalid face fallback"))
    momentum = zeros(T, width, height)
    weights = zeros(T, width, height)
    periodic_x = :x in domain.periodic_axes
    periodic_y = :y in domain.periodic_axes
    unique_width = duplicate_x ? width - 1 : width
    unique_height = duplicate_y ? height - 1 : height
    for particle in axes(positions, 2)
        gx = (positions[1, particle] - domain.bounds[1][1]) / dx(domain) + gx_offset
        gy = (positions[2, particle] - domain.bounds[2][1]) / dy(domain) + gy_offset
        base_x = floor(Int, gx - T(0.5))
        base_y = floor(Int, gy - T(0.5))
        for offset_y in 0:2
            source_y = base_y + offset_y
            target_y_zero = periodic_y ? mod(source_y, unique_height) : source_y
            0 <= target_y_zero < height || continue
            weight_y = quadratic_bspline_weight(gy - T(source_y))
            for offset_x in 0:2
                source_x = base_x + offset_x
                target_x_zero = periodic_x ? mod(source_x, unique_width) : source_x
                0 <= target_x_zero < width || continue
                weight = weight_y * quadratic_bspline_weight(gx - T(source_x))
                target_x = target_x_zero + 1
                target_y = target_y_zero + 1
                weights[target_x, target_y] += weight
                momentum[target_x, target_y] += weight * values[particle]
            end
        end
    end
    output = similar(fallback)
    unsupported = 0
    for index in eachindex(output)
        if weights[index] > T(1.0e-12)
            output[index] = momentum[index] / weights[index]
        else
            output[index] = fallback[index]
            unsupported += 1
        end
    end
    duplicate_x && (output[end, :] .= output[1, :])
    duplicate_y && (output[:, end] .= output[:, 1])
    return output, unsupported
end

function _gather_face_component(
    field::AbstractMatrix{T},
    positions::AbstractMatrix{T},
    domain::DomainSpec{2,T},
    gx_offset::T,
    gy_offset::T,
    duplicate_x::Bool,
    duplicate_y::Bool,
) where {T<:AbstractFloat}
    width, height = size(field)
    periodic_x = :x in domain.periodic_axes
    periodic_y = :y in domain.periodic_axes
    unique_width = duplicate_x ? width - 1 : width
    unique_height = duplicate_y ? height - 1 : height
    output = Vector{T}(undef, size(positions, 2))
    for particle in axes(positions, 2)
        gx = (positions[1, particle] - domain.bounds[1][1]) / dx(domain) + gx_offset
        gy = (positions[2, particle] - domain.bounds[2][1]) / dy(domain) + gy_offset
        base_x = floor(Int, gx - T(0.5))
        base_y = floor(Int, gy - T(0.5))
        value = zero(T)
        weight_sum = zero(T)
        for offset_y in 0:2
            source_y = base_y + offset_y
            target_y_zero = periodic_y ? mod(source_y, unique_height) : source_y
            0 <= target_y_zero < height || continue
            weight_y = quadratic_bspline_weight(gy - T(source_y))
            for offset_x in 0:2
                source_x = base_x + offset_x
                target_x_zero = periodic_x ? mod(source_x, unique_width) : source_x
                0 <= target_x_zero < width || continue
                weight = weight_y * quadratic_bspline_weight(gx - T(source_x))
                value += weight * field[target_x_zero + 1, target_y_zero + 1]
                weight_sum += weight
            end
        end
        output[particle] = value / max(weight_sum, T(1.0e-12))
    end
    return output
end

function particle_to_faces(
    positions::AbstractMatrix{T},
    velocities::AbstractMatrix{T},
    domain::DomainSpec{2,T},
    fallback_u::AbstractMatrix{T},
    fallback_v::AbstractMatrix{T},
) where {T<:AbstractFloat}
    size(positions) == size(velocities) || throw(DimensionMismatch("particle arrays disagree"))
    u, unsupported_u = _scatter_face_component(
        positions, view(velocities, 1, :), domain, nx(domain) + 1, ny(domain),
        zero(T), T(-0.5), :x in domain.periodic_axes, false, fallback_u,
    )
    v, unsupported_v = _scatter_face_component(
        positions, view(velocities, 2, :), domain, nx(domain), ny(domain) + 1,
        T(-0.5), zero(T), false, :y in domain.periodic_axes, fallback_v,
    )
    unsupported_fraction = T(unsupported_u + unsupported_v) / T(length(u) + length(v))
    return u, v, unsupported_fraction
end

function faces_to_particle(
    u::AbstractMatrix{T},
    v::AbstractMatrix{T},
    positions::AbstractMatrix{T},
    domain::DomainSpec{2,T},
) where {T<:AbstractFloat}
    size(u) == (nx(domain) + 1, ny(domain)) || throw(DimensionMismatch("invalid u faces"))
    size(v) == (nx(domain), ny(domain) + 1) || throw(DimensionMismatch("invalid v faces"))
    output = Matrix{T}(undef, 2, size(positions, 2))
    output[1, :] .= _gather_face_component(
        u, positions, domain, zero(T), T(-0.5), :x in domain.periodic_axes, false,
    )
    output[2, :] .= _gather_face_component(
        v, positions, domain, T(-0.5), zero(T), false, :y in domain.periodic_axes,
    )
    return output
end

function particle_cell_ids(
    positions::AbstractMatrix{T},
    domain::DomainSpec{2,T},
) where {T<:AbstractFloat}
    size(positions, 1) == 2 || throw(DimensionMismatch("positions must have shape 2 × particle"))
    identifiers = Vector{Int}(undef, size(positions, 2))
    for particle in axes(positions, 2)
        i = clamp(floor(Int, (positions[1, particle] - domain.bounds[1][1]) / dx(domain)) + 1, 1, nx(domain))
        j = clamp(floor(Int, (positions[2, particle] - domain.bounds[2][1]) / dy(domain)) + 1, 1, ny(domain))
        identifiers[particle] = i + (j - 1) * nx(domain)
    end
    return identifiers
end

function particle_cell_counts(
    positions::AbstractMatrix{T},
    domain::DomainSpec{2,T},
) where {T<:AbstractFloat}
    counts = zeros(Int, nx(domain) * ny(domain))
    for identifier in particle_cell_ids(positions, domain)
        counts[identifier] += 1
    end
    return reshape(counts, nx(domain), ny(domain))
end
