function _grid_indices(coordinate::T, count::Int, extent::Int, periodic::Bool) where {T}
    if periodic
        lower_zero = floor(Int, coordinate)
        fraction = coordinate - T(lower_zero)
        lower = mod(lower_zero, count) + 1
        upper = mod(lower_zero + 1, count) + 1
        return lower, upper, fraction
    end
    clipped = clamp(coordinate, zero(T), T(extent - 1))
    lower_zero = floor(Int, clipped)
    lower = lower_zero + 1
    upper = min(lower + 1, extent)
    return lower, upper, clipped - T(lower_zero)
end

function sample_staggered_scalar(
    field::AbstractMatrix{T},
    points::AbstractMatrix{T},
    domain::DomainSpec{2,T},
    offset::NTuple{2,Real},
) where {T<:AbstractFloat}
    size(points, 1) == 2 || throw(DimensionMismatch("points must have shape 2 × point"))
    output = Vector{T}(undef, size(points, 2))
    x0 = domain.bounds[1][1]
    y0 = domain.bounds[2][1]
    periodic_x = :x in domain.periodic_axes
    periodic_y = :y in domain.periodic_axes
    for point_index in axes(points, 2)
        gx = (points[1, point_index] - x0) / dx(domain) - T(offset[1])
        gy = (points[2, point_index] - y0) / dy(domain) - T(offset[2])
        i0, i1, tx = _grid_indices(gx, nx(domain), size(field, 1), periodic_x)
        j0, j1, ty = _grid_indices(gy, ny(domain), size(field, 2), periodic_y)
        output[point_index] =
            (one(T) - tx) * (one(T) - ty) * field[i0, j0] +
            tx * (one(T) - ty) * field[i1, j0] +
            (one(T) - tx) * ty * field[i0, j1] +
            tx * ty * field[i1, j1]
    end
    return output
end

function sample_scalar(
    field::AbstractMatrix{T},
    points::AbstractMatrix{T},
    domain::DomainSpec{2,T},
) where {T<:AbstractFloat}
    size(field) == (nx(domain), ny(domain)) ||
        throw(DimensionMismatch("scalar field does not match the domain"))
    return sample_staggered_scalar(field, points, domain, (0.5, 0.5))
end

function sample_velocity_field(
    velocity::AbstractArray{T,3},
    points::AbstractMatrix{T},
    domain::DomainSpec{2,T},
) where {T<:AbstractFloat}
    size(velocity) == (nx(domain), ny(domain), 2) ||
        throw(DimensionMismatch("velocity field does not match the domain"))
    output = Matrix{T}(undef, 2, size(points, 2))
    output[1, :] = sample_scalar(view(velocity, :, :, 1), points, domain)
    output[2, :] = sample_scalar(view(velocity, :, :, 2), points, domain)
    return output
end

function rk2_backtrace(
    velocity::AbstractArray{T,3},
    points::AbstractMatrix{T},
    timestep::T,
    domain::DomainSpec{2,T},
) where {T<:AbstractFloat}
    initial = sample_velocity_field(velocity, points, domain)
    midpoint = points .- T(0.5) .* timestep .* initial
    midpoint_velocity = sample_velocity_field(velocity, midpoint, domain)
    return points .- timestep .* midpoint_velocity
end
