function kinetic_energy(velocity::AbstractArray{T,3}) where {T<:AbstractFloat}
    return T(0.5) * sum(abs2, velocity) / T(size(velocity, 1) * size(velocity, 2))
end

function momentum(velocity::AbstractArray{T,3}) where {T<:AbstractFloat}
    cell_count = T(size(velocity, 1) * size(velocity, 2))
    return SVector{2,T}(
        sum(view(velocity, :, :, 1)) / cell_count,
        sum(view(velocity, :, :, 2)) / cell_count,
    )
end

function vorticity(velocity::AbstractArray{T,3}, domain::DomainSpec{2,T}) where {T}
    output = Matrix{T}(undef, nx(domain), ny(domain))
    for j in 1:ny(domain), i in 1:nx(domain)
        im = max(i - 1, 1)
        ip = min(i + 1, nx(domain))
        jm = max(j - 1, 1)
        jp = min(j + 1, ny(domain))
        dv_dx = (velocity[ip, j, 2] - velocity[im, j, 2]) / T(ip - im) / dx(domain)
        du_dy = (velocity[i, jp, 1] - velocity[i, jm, 1]) / T(jp - jm) / dy(domain)
        output[i, j] = dv_dx - du_dy
    end
    return output
end

function enstrophy(velocity::AbstractArray{T,3}, domain::DomainSpec{2,T}) where {T}
    omega = vorticity(velocity, domain)
    return T(0.5) * sum(abs2, omega) / T(length(omega))
end

function divergence_l2(velocity::AbstractArray{T,3}, domain::DomainSpec{2,T}) where {T}
    u, v = cell_to_faces(velocity)
    divergence = face_divergence(u, v, domain)
    return sqrt(sum(abs2, divergence) / T(length(divergence)))
end

function solid_leakage(velocity::AbstractArray{T,3}, solid::AbstractMatrix{Bool}) where {T}
    maximum_squared = zero(T)
    for j in axes(solid, 2), i in axes(solid, 1)
        solid[i, j] || continue
        maximum_squared = max(
            maximum_squared,
            velocity[i, j, 1]^2 + velocity[i, j, 2]^2,
        )
    end
    return sqrt(maximum_squared)
end

function wake_width(
    velocity::AbstractArray{T,3},
    domain::DomainSpec{2,T},
    pivot_x::Real;
    threshold::Real = 0.1,
) where {T}
    active_rows = falses(ny(domain))
    x0 = domain.bounds[1][1]
    for j in 1:ny(domain), i in 1:nx(domain)
        x = x0 + (T(i) - T(0.5)) * dx(domain)
        x > T(pivot_x) + one(T) || continue
        one(T) - velocity[i, j, 1] > T(threshold) && (active_rows[j] = true)
    end
    return count(active_rows) * dy(domain)
end

function recirculation_area(
    velocity::AbstractArray{T,3},
    domain::DomainSpec{2,T},
    pivot_x::Real,
) where {T}
    count_value = 0
    x0 = domain.bounds[1][1]
    for j in 1:ny(domain), i in 1:nx(domain)
        x = x0 + (T(i) - T(0.5)) * dx(domain)
        x > T(pivot_x) && velocity[i, j, 1] < zero(T) && (count_value += 1)
    end
    return T(count_value) * dx(domain) * dy(domain)
end
