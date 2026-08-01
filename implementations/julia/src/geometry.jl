struct FoilSpec{T<:AbstractFloat}
    naca::String
    chord::T
    pivot::SVector{2,T}

    function FoilSpec(naca::AbstractString, chord::T, pivot::SVector{2,T}) where {T<:AbstractFloat}
        occursin(r"^[0-9]{4}$", naca) || throw(ArgumentError("NACA code must contain four digits"))
        chord > zero(T) || throw(ArgumentError("foil chord must be positive"))
        return new{T}(String(naca), chord, pivot)
    end
end

struct NacaFoil{T<:AbstractFloat}
    spec::FoilSpec{T}
end

maximum_camber(foil::NacaFoil{T}) where {T} = T(parse(Int, foil.spec.naca[1:1])) / T(100)
camber_position(foil::NacaFoil{T}) where {T} = T(parse(Int, foil.spec.naca[2:2])) / T(10)
thickness(foil::NacaFoil{T}) where {T} = T(parse(Int, foil.spec.naca[3:4])) / T(100)

function surfaces(foil::NacaFoil{T}, x_local::AbstractVector{T}) where {T}
    chord = foil.spec.chord
    x = clamp.(x_local ./ chord, zero(T), one(T))
    yt = T(5) * thickness(foil) * chord .* (
        T(0.2969) .* sqrt.(max.(x, zero(T))) .-
        T(0.1260) .* x .-
        T(0.3516) .* x .^ 2 .+
        T(0.2843) .* x .^ 3 .-
        T(0.1036) .* x .^ 4
    )
    m = maximum_camber(foil)
    p = camber_position(foil)
    camber = zeros(T, length(x))
    if m > 0 && p > 0
        for index in eachindex(x)
            value = x[index]
            camber[index] = if value < p
                m / p^2 * (T(2) * p * value - value^2)
            else
                m / (one(T) - p)^2 * (
                    (one(T) - T(2) * p) + T(2) * p * value - value^2
                )
            end
        end
        camber .*= chord
    end
    return camber + yt, camber - yt
end

function to_local(foil::NacaFoil{T}, point::SVector{2,T}, angle_degrees::Real) where {T}
    angle = T(deg2rad(angle_degrees))
    cosine = cos(angle)
    sine = sin(angle)
    translated = point - foil.spec.pivot
    return SVector(
        cosine * translated[1] + sine * translated[2] + T(0.25) * foil.spec.chord,
        -sine * translated[1] + cosine * translated[2],
    )
end

function signed_distance(foil::NacaFoil{T}, point::SVector{2,T}, angle_degrees::Real) where {T}
    local_point = to_local(foil, point, angle_degrees)
    upper, lower = surfaces(foil, T[local_point[1]])
    x, y = local_point
    vertical_outside = max(y - upper[1], lower[1] - y)
    vertical_inside = -min(upper[1] - y, y - lower[1])
    vertical = lower[1] <= y <= upper[1] ? vertical_inside : vertical_outside
    if 0 <= x <= foil.spec.chord
        return vertical
    end
    outside_x = max(max(-x, x - foil.spec.chord), zero(T))
    return hypot(outside_x, max(vertical, zero(T)))
end

function signed_distance(
    foil::NacaFoil{T},
    points::AbstractMatrix{T},
    angle_degrees::Real,
) where {T}
    size(points, 2) == 2 || throw(DimensionMismatch("points must have shape point × 2"))
    return [signed_distance(foil, SVector{2,T}(points[index, :]), angle_degrees) for index in axes(points, 1)]
end

function normals(foil::NacaFoil{T}, points::AbstractMatrix{T}, angle_degrees::Real) where {T}
    size(points, 2) == 2 || throw(DimensionMismatch("points must have shape point × 2"))
    epsilon = max(foil.spec.chord * T(1.0e-4), T(1.0e-6))
    output = Matrix{T}(undef, size(points, 1), 2)
    for index in axes(points, 1)
        point = SVector{2,T}(points[index, :])
        dx = signed_distance(foil, point + SVector(epsilon, zero(T)), angle_degrees) -
             signed_distance(foil, point - SVector(epsilon, zero(T)), angle_degrees)
        dy = signed_distance(foil, point + SVector(zero(T), epsilon), angle_degrees) -
             signed_distance(foil, point - SVector(zero(T), epsilon), angle_degrees)
        gradient = SVector(dx, dy)
        length = sqrt(sum(abs2, gradient))
        if length < epsilon
            angle = T(deg2rad(angle_degrees))
            gradient = SVector(-sin(angle), cos(angle))
            length = one(T)
        end
        output[index, :] = gradient / max(length, epsilon)
    end
    return output
end
