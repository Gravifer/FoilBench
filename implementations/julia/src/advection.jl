function _cell_points(domain::DomainSpec{2,T}) where {T}
    centers = cell_centers(domain)
    points = Matrix{T}(undef, 2, nx(domain) * ny(domain))
    point_index = 1
    for j in 1:ny(domain), i in 1:nx(domain)
        points[1, point_index] = centers[i, j, 1]
        points[2, point_index] = centers[i, j, 2]
        point_index += 1
    end
    return points
end

function _points_to_velocity(values::AbstractMatrix{T}, domain::DomainSpec{2,T}) where {T}
    velocity = Array{T,3}(undef, nx(domain), ny(domain), 2)
    point_index = 1
    for j in 1:ny(domain), i in 1:nx(domain)
        velocity[i, j, 1] = values[1, point_index]
        velocity[i, j, 2] = values[2, point_index]
        point_index += 1
    end
    return velocity
end

function _local_velocity_bounds(
    velocity::AbstractArray{T,3},
    domain::DomainSpec{2,T},
) where {T}
    lower = copy(velocity)
    upper = copy(velocity)
    periodic_x = :x in domain.periodic_axes
    periodic_y = :y in domain.periodic_axes
    for component in 1:2, j in 1:ny(domain), i in 1:nx(domain)
        for offset_y in -1:1, offset_x in -1:1
            neighbor_i = periodic_x ? mod1(i + offset_x, nx(domain)) : clamp(i + offset_x, 1, nx(domain))
            neighbor_j = periodic_y ? mod1(j + offset_y, ny(domain)) : clamp(j + offset_y, 1, ny(domain))
            value = velocity[neighbor_i, neighbor_j, component]
            lower[i, j, component] = min(lower[i, j, component], value)
            upper[i, j, component] = max(upper[i, j, component], value)
        end
    end
    return lower, upper
end

function advect_velocity(
    velocity::AbstractArray{T,3},
    timestep::T,
    domain::DomainSpec{2,T};
    maccormack::Bool = true,
) where {T<:AbstractFloat}
    points = _cell_points(domain)
    departure = rk2_backtrace(velocity, points, timestep, domain)
    first = _points_to_velocity(sample_velocity_field(velocity, departure, domain), domain)
    maccormack || return first
    forward_points = rk2_backtrace(first, points, -timestep, domain)
    forward = _points_to_velocity(sample_velocity_field(first, forward_points, domain), domain)
    lower, upper = _local_velocity_bounds(velocity, domain)
    corrected = similar(first)
    for index in eachindex(first)
        selected_lower = min(lower[index], first[index])
        selected_upper = max(upper[index], first[index])
        corrected[index] = clamp(
            first[index] + T(0.5) * (velocity[index] - forward[index]),
            selected_lower,
            selected_upper,
        )
    end
    return corrected
end

function _face_points(domain::DomainSpec{2,T}) where {T}
    u_points = Matrix{T}(undef, 2, (nx(domain) + 1) * ny(domain))
    v_points = Matrix{T}(undef, 2, nx(domain) * (ny(domain) + 1))
    x0 = domain.bounds[1][1]
    y0 = domain.bounds[2][1]
    index = 1
    for j in 1:ny(domain), i in 1:(nx(domain) + 1)
        u_points[1, index] = x0 + T(i - 1) * dx(domain)
        u_points[2, index] = y0 + (T(j) - T(0.5)) * dy(domain)
        index += 1
    end
    index = 1
    for j in 1:(ny(domain) + 1), i in 1:nx(domain)
        v_points[1, index] = x0 + (T(i) - T(0.5)) * dx(domain)
        v_points[2, index] = y0 + T(j - 1) * dy(domain)
        index += 1
    end
    return u_points, v_points
end

function _reshape_face(values::AbstractVector{T}, extent_x::Int, extent_y::Int) where {T}
    output = Matrix{T}(undef, extent_x, extent_y)
    index = 1
    for j in 1:extent_y, i in 1:extent_x
        output[i, j] = values[index]
        index += 1
    end
    return output
end

function _face_local_bounds(field::AbstractMatrix{T}) where {T}
    lower = copy(field)
    upper = copy(field)
    for j in axes(field, 2), i in axes(field, 1), offset_y in -1:1, offset_x in -1:1
        neighbor_i = clamp(i + offset_x, 1, size(field, 1))
        neighbor_j = clamp(j + offset_y, 1, size(field, 2))
        value = field[neighbor_i, neighbor_j]
        lower[i, j] = min(lower[i, j], value)
        upper[i, j] = max(upper[i, j], value)
    end
    return lower, upper
end

function _advect_face_component(
    field::AbstractMatrix{T},
    velocity::AbstractArray{T,3},
    points::AbstractMatrix{T},
    offset::NTuple{2,Real},
    timestep::T,
    domain::DomainSpec{2,T},
) where {T}
    departure = rk2_backtrace(velocity, points, timestep, domain)
    values = sample_staggered_scalar(field, departure, domain, offset)
    return _reshape_face(values, size(field, 1), size(field, 2))
end

function advect_faces(
    u::AbstractMatrix{T},
    v::AbstractMatrix{T},
    timestep::T,
    domain::DomainSpec{2,T};
    maccormack::Bool = true,
) where {T<:AbstractFloat}
    velocity = faces_to_cell(u, v)
    u_points, v_points = _face_points(domain)
    first_u = _advect_face_component(u, velocity, u_points, (0.0, 0.5), timestep, domain)
    first_v = _advect_face_component(v, velocity, v_points, (0.5, 0.0), timestep, domain)
    maccormack || return first_u, first_v
    forward_velocity = faces_to_cell(first_u, first_v)
    forward_u = _advect_face_component(first_u, forward_velocity, u_points, (0.0, 0.5), -timestep, domain)
    forward_v = _advect_face_component(first_v, forward_velocity, v_points, (0.5, 0.0), -timestep, domain)
    lower_u, upper_u = _face_local_bounds(u)
    lower_v, upper_v = _face_local_bounds(v)
    corrected_u = clamp.(first_u .+ T(0.5) .* (u .- forward_u), lower_u, upper_u)
    corrected_v = clamp.(first_v .+ T(0.5) .* (v .- forward_v), lower_v, upper_v)
    return corrected_u, corrected_v
end

function _derivative_x(
    field::AbstractMatrix{T},
    spacing::T,
    periodic::Bool;
    duplicate_endpoint::Bool = false,
) where {T}
    output = similar(field)
    logical_size = size(field, 1) - (periodic && duplicate_endpoint ? 1 : 0)
    for j in axes(field, 2), i in axes(field, 1)
        if periodic
            logical_i = i > logical_size ? 1 : i
            output[i, j] = (field[mod1(logical_i + 1, logical_size), j] -
                            field[mod1(logical_i - 1, logical_size), j]) /
                           (T(2) * spacing)
        elseif i == 1
            output[i, j] = (field[2, j] - field[1, j]) / spacing
        elseif i == size(field, 1)
            output[i, j] = (field[end, j] - field[end - 1, j]) / spacing
        else
            output[i, j] = (field[i + 1, j] - field[i - 1, j]) / (T(2) * spacing)
        end
    end
    return output
end

function _derivative_y(
    field::AbstractMatrix{T},
    spacing::T,
    periodic::Bool;
    duplicate_endpoint::Bool = false,
) where {T}
    output = similar(field)
    logical_size = size(field, 2) - (periodic && duplicate_endpoint ? 1 : 0)
    for j in axes(field, 2), i in axes(field, 1)
        if periodic
            logical_j = j > logical_size ? 1 : j
            output[i, j] = (field[i, mod1(logical_j + 1, logical_size)] -
                            field[i, mod1(logical_j - 1, logical_size)]) /
                           (T(2) * spacing)
        elseif j == 1
            output[i, j] = (field[i, 2] - field[i, 1]) / spacing
        elseif j == size(field, 2)
            output[i, j] = (field[i, end] - field[i, end - 1]) / spacing
        else
            output[i, j] = (field[i, j + 1] - field[i, j - 1]) / (T(2) * spacing)
        end
    end
    return output
end

function _skew_symmetric_convection(
    u::AbstractMatrix{T},
    v::AbstractMatrix{T},
    domain::DomainSpec{2,T},
) where {T}
    cell = faces_to_cell(u, v)
    v_on_u, u_on_v = cell_to_faces(cell[:, :, [2, 1]])
    periodic_x = :x in domain.periodic_axes
    periodic_y = :y in domain.periodic_axes
    if periodic_x
        periodic_v = T(0.5) .* (cell[end, :, 2] .+ cell[1, :, 2])
        v_on_u[1, :] .= periodic_v
        v_on_u[end, :] .= periodic_v
    end
    if periodic_y
        periodic_u = T(0.5) .* (cell[:, end, 1] .+ cell[:, 1, 1])
        u_on_v[:, 1] .= periodic_u
        u_on_v[:, end] .= periodic_u
    end
    du_dx = _derivative_x(u, dx(domain), periodic_x; duplicate_endpoint = periodic_x)
    du_dy = _derivative_y(u, dy(domain), :y in domain.periodic_axes)
    dv_dx = _derivative_x(v, dx(domain), :x in domain.periodic_axes)
    dv_dy = _derivative_y(v, dy(domain), periodic_y; duplicate_endpoint = periodic_y)
    advective_u = u .* du_dx .+ v_on_u .* du_dy
    advective_v = u_on_v .* dv_dx .+ v .* dv_dy
    conservative_u = _derivative_x(
        u .* u, dx(domain), periodic_x; duplicate_endpoint = periodic_x,
    ) .+
        _derivative_y(v_on_u .* u, dy(domain), :y in domain.periodic_axes)
    conservative_v = _derivative_x(u_on_v .* v, dx(domain), :x in domain.periodic_axes) .+
        _derivative_y(v .* v, dy(domain), periodic_y; duplicate_endpoint = periodic_y)
    return T(0.5) .* (advective_u .+ conservative_u),
        T(0.5) .* (advective_v .+ conservative_v)
end

function skew_face_advection_rate(
    u::AbstractMatrix{T},
    v::AbstractMatrix{T},
    domain::DomainSpec{2,T},
) where {T<:AbstractFloat}
    cell = faces_to_cell(u, v)
    v_on_u, u_on_v = cell_to_faces(cell[:, :, [2, 1]])
    if :x in domain.periodic_axes
        periodic_v = T(0.5) .* (cell[end, :, 2] .+ cell[1, :, 2])
        v_on_u[1, :] .= periodic_v
        v_on_u[end, :] .= periodic_v
    end
    if :y in domain.periodic_axes
        periodic_u = T(0.5) .* (cell[:, end, 1] .+ cell[:, 1, 1])
        u_on_v[:, 1] .= periodic_u
        u_on_v[:, end] .= periodic_u
    end
    selected = zero(T)
    for index in eachindex(u, v_on_u)
        selected = max(selected, abs(u[index]) / dx(domain) + abs(v_on_u[index]) / dy(domain))
    end
    for index in eachindex(v, u_on_v)
        selected = max(selected, abs(u_on_v[index]) / dx(domain) + abs(v[index]) / dy(domain))
    end
    return selected
end

function advect_faces_skew_rk2(
    u::AbstractMatrix{T},
    v::AbstractMatrix{T},
    timestep::T,
    domain::DomainSpec{2,T},
    solid::AbstractMatrix{Bool},
    wall_velocity::AbstractArray{T,3},
    freestream::SVector{2,T},
) where {T<:AbstractFloat}
    first_u, first_v = _skew_symmetric_convection(u, v, domain)
    midpoint_u = u .- T(0.5) .* timestep .* first_u
    midpoint_v = v .- T(0.5) .* timestep .* first_v
    apply_domain_boundaries!(midpoint_u, midpoint_v, domain, freestream)
    enforce_solid_faces!(midpoint_u, midpoint_v, solid, wall_velocity)
    second_u, second_v = _skew_symmetric_convection(midpoint_u, midpoint_v, domain)
    advected_u = u .- timestep .* second_u
    advected_v = v .- timestep .* second_v
    apply_domain_boundaries!(advected_u, advected_v, domain, freestream)
    enforce_solid_faces!(advected_u, advected_v, solid, wall_velocity)
    return advected_u, advected_v
end
