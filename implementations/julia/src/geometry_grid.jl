function cell_centers(domain::DomainSpec{2,T}) where {T}
    output = Array{T,3}(undef, nx(domain), ny(domain), 2)
    x0 = domain.bounds[1][1]
    y0 = domain.bounds[2][1]
    for j in 1:ny(domain), i in 1:nx(domain)
        output[i, j, 1] = x0 + (T(i) - T(0.5)) * dx(domain)
        output[i, j, 2] = y0 + (T(j) - T(0.5)) * dy(domain)
    end
    return output
end

function solid_mask(foil::NacaFoil{2,T}, domain::DomainSpec{2,T}, angle_degrees::Real) where {T}
    centers = cell_centers(domain)
    mask = BitMatrix(undef, nx(domain), ny(domain))
    for j in 1:ny(domain), i in 1:nx(domain)
        point = SVector{2,T}(centers[i, j, 1], centers[i, j, 2])
        mask[i, j] = signed_distance(foil, point, angle_degrees) <= zero(T)
    end
    return mask
end

function wall_velocity_grid(
    foil::NacaFoil{2,T},
    domain::DomainSpec{2,T},
    control::ControlState,
) where {T}
    centers = cell_centers(domain)
    output = Array{T,3}(undef, nx(domain), ny(domain), 2)
    omega = T(deg2rad(control.angular_velocity_degrees))
    for j in 1:ny(domain), i in 1:nx(domain)
        relative_x = centers[i, j, 1] - foil.spec.pivot[1]
        relative_y = centers[i, j, 2] - foil.spec.pivot[2]
        output[i, j, 1] = -omega * relative_y
        output[i, j, 2] = omega * relative_x
    end
    return output
end

function foil_outline(
    foil::NacaFoil{2,T},
    angle_degrees::Real;
    samples::Int = 256,
) where {T}
    samples >= 8 || throw(ArgumentError("foil outline requires at least eight samples"))
    half_samples = samples ÷ 2
    beta = range(zero(T), T(pi); length = half_samples)
    x = @. foil.spec.chord * T(0.5) * (one(T) - cos(beta))
    upper, lower = surfaces(foil, collect(x))
    local_points = Matrix{T}(undef, 2, 2 * half_samples)
    for index in 1:half_samples
        local_points[1, index] = x[index]
        local_points[2, index] = upper[index]
        reverse_index = half_samples - index + 1
        local_points[1, half_samples + index] = x[reverse_index]
        local_points[2, half_samples + index] = lower[reverse_index]
    end
    angle = T(deg2rad(angle_degrees))
    cosine = cos(angle)
    sine = sin(angle)
    output = similar(local_points)
    for index in axes(local_points, 2)
        shifted_x = local_points[1, index] - T(0.25) * foil.spec.chord
        local_y = local_points[2, index]
        output[1, index] = cosine * shifted_x - sine * local_y + foil.spec.pivot[1]
        output[2, index] = sine * shifted_x + cosine * local_y + foil.spec.pivot[2]
    end
    return output
end
function wall_velocity(
    foil::NacaFoil{2,T},
    points::AbstractMatrix{T},
    control::ControlState,
) where {T}
    size(points, 2) == 2 || throw(DimensionMismatch("points must have shape point × 2"))
    relative = points .- permutedims(foil.spec.pivot)
    omega = T(deg2rad(control.angular_velocity_degrees))
    output = similar(points)
    output[:, 1] .= .-omega .* relative[:, 2]
    output[:, 2] .= omega .* relative[:, 1]
    return output
end
