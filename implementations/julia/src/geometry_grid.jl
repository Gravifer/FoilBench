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
