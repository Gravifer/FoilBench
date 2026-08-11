function _pcg(
    apply_operator!::F,
    right_hand_side::AbstractMatrix{T},
    inverse_diagonal::AbstractMatrix{T};
    tolerance::T,
    max_iterations::Int,
) where {F,T<:AbstractFloat}
    all(isfinite, right_hand_side) ||
        throw(NumericalFailure(:nonfinite_state, "linear solve RHS must be finite"))
    solution = zeros(T, size(right_hand_side))
    residual = copy(right_hand_side)
    preconditioned = residual .* inverse_diagonal
    direction = copy(preconditioned)
    operator_direction = similar(direction)
    residual_dot = dot(residual, preconditioned)
    right_norm = sqrt(dot(right_hand_side, right_hand_side))
    right_norm <= eps(T) && return solution, 0, true
    threshold = max(tolerance * right_norm, eps(T) * sqrt(T(length(right_hand_side))))
    for iteration in 1:max_iterations
        apply_operator!(operator_direction, direction)
        denominator = dot(direction, operator_direction)
        isfinite(denominator) && denominator > zero(T) ||
            throw(NumericalFailure(
                :projection_failure,
                "preconditioned CG encountered a non-positive operator",
            ))
        alpha = residual_dot / denominator
        @. solution += alpha * direction
        @. residual -= alpha * operator_direction
        all(isfinite, solution) && all(isfinite, residual) ||
            throw(NumericalFailure(
                :nonfinite_state,
                "preconditioned CG produced non-finite state",
            ))
        sqrt(dot(residual, residual)) <= threshold && return solution, iteration, true
        @. preconditioned = residual * inverse_diagonal
        next_residual_dot = dot(residual, preconditioned)
        beta = next_residual_dot / residual_dot
        @. direction = preconditioned + beta * direction
        residual_dot = next_residual_dot
    end
    return solution, max_iterations, false
end

function _pressure_diagonal(fluid::AbstractMatrix{Bool}, domain::DomainSpec{2,T}) where {T}
    diagonal = Matrix{T}(undef, size(fluid))
    inv_dx2 = inv(dx(domain)^2)
    inv_dy2 = inv(dy(domain)^2)
    periodic_x = :x in domain.periodic_axes
    periodic_y = :y in domain.periodic_axes
    for j in axes(fluid, 2), i in axes(fluid, 1)
        if !fluid[i, j]
            diagonal[i, j] = one(T)
            continue
        end
        value = zero(T)
        if i > 1
            fluid[i - 1, j] && (value += inv_dx2)
        elseif periodic_x
            fluid[end, j] && (value += inv_dx2)
        else
            value += inv_dx2
        end
        if i < size(fluid, 1)
            fluid[i + 1, j] && (value += inv_dx2)
        elseif periodic_x
            fluid[1, j] && (value += inv_dx2)
        else
            value += inv_dx2
        end
        if j > 1
            fluid[i, j - 1] && (value += inv_dy2)
        elseif periodic_y
            fluid[i, end] && (value += inv_dy2)
        else
            value += inv_dy2
        end
        if j < size(fluid, 2)
            fluid[i, j + 1] && (value += inv_dy2)
        elseif periodic_y
            fluid[i, 1] && (value += inv_dy2)
        else
            value += inv_dy2
        end
        diagonal[i, j] = max(value, eps(T))
    end
    return diagonal
end

function _apply_pressure_operator!(
    output::AbstractMatrix{T},
    pressure::AbstractMatrix{T},
    fluid::AbstractMatrix{Bool},
    diagonal::AbstractMatrix{T},
    domain::DomainSpec{2,T},
) where {T}
    inv_dx2 = inv(dx(domain)^2)
    inv_dy2 = inv(dy(domain)^2)
    periodic_x = :x in domain.periodic_axes
    periodic_y = :y in domain.periodic_axes
    for j in axes(fluid, 2), i in axes(fluid, 1)
        if !fluid[i, j]
            output[i, j] = pressure[i, j]
            continue
        end
        value = diagonal[i, j] * pressure[i, j]
        left = i > 1 ? i - 1 : periodic_x ? size(fluid, 1) : 0
        right = i < size(fluid, 1) ? i + 1 : periodic_x ? 1 : 0
        lower = j > 1 ? j - 1 : periodic_y ? size(fluid, 2) : 0
        upper = j < size(fluid, 2) ? j + 1 : periodic_y ? 1 : 0
        left > 0 && fluid[left, j] && (value -= inv_dx2 * pressure[left, j])
        right > 0 && fluid[right, j] && (value -= inv_dx2 * pressure[right, j])
        lower > 0 && fluid[i, lower] && (value -= inv_dy2 * pressure[i, lower])
        upper > 0 && fluid[i, upper] && (value -= inv_dy2 * pressure[i, upper])
        output[i, j] = value
    end
    return nothing
end

function solve_masked_poisson(
    right_hand_side::AbstractMatrix{T},
    fluid::AbstractMatrix{Bool},
    domain::DomainSpec{2,T};
    tolerance::Real = 1.0e-5,
    max_iterations::Int = 160,
) where {T<:AbstractFloat}
    size(right_hand_side) == size(fluid) == (nx(domain), ny(domain)) ||
        throw(DimensionMismatch("pressure arrays must match the domain"))
    compatible = Matrix{T}(right_hand_side)
    compatible[.!fluid] .= zero(T)
    if :x in domain.periodic_axes && :y in domain.periodic_axes && any(fluid)
        compatible[fluid] .-= sum(compatible[fluid]) / T(count(fluid))
    end
    diagonal = _pressure_diagonal(fluid, domain)
    inverse_diagonal = inv.(diagonal)
    apply_operator! = (output, pressure) ->
        _apply_pressure_operator!(output, pressure, fluid, diagonal, domain)
    pressure, iterations, converged = _pcg(
        apply_operator!,
        compatible,
        inverse_diagonal;
        tolerance = T(tolerance),
        max_iterations = max_iterations,
    )
    if any(fluid)
        pressure[fluid] .-= sum(pressure[fluid]) / T(count(fluid))
    end
    return pressure, iterations, converged
end

function project_faces!(
    u::AbstractMatrix{T},
    v::AbstractMatrix{T},
    domain::DomainSpec{2,T},
    solid::AbstractMatrix{Bool},
    wall_velocity::AbstractArray{T,3},
    freestream::SVector{2,T},
    timestep::T;
    channel_walls::Bool = false,
    tolerance::Real = 1.0e-5,
    max_iterations::Int = 160,
) where {T<:AbstractFloat}
    timestep > zero(T) || throw(ArgumentError("projection timestep must be positive"))
    apply_domain_boundaries!(u, v, domain, freestream; channel_walls)
    enforce_solid_faces!(u, v, solid, wall_velocity)
    divergence = face_divergence(u, v, domain)
    fluid = .!solid
    right_hand_side = similar(divergence)
    for index in eachindex(divergence)
        right_hand_side[index] = fluid[index] ? -divergence[index] / timestep : zero(T)
    end
    pressure, iterations, converged = solve_masked_poisson(
        right_hand_side,
        fluid,
        domain;
        tolerance,
        max_iterations,
    )
    for j in 1:ny(domain), i in 2:nx(domain)
        u[i, j] -= timestep * (pressure[i, j] - pressure[i - 1, j]) / dx(domain)
    end
    for j in 2:ny(domain), i in 1:nx(domain)
        v[i, j] -= timestep * (pressure[i, j] - pressure[i, j - 1]) / dy(domain)
    end
    if :x in domain.periodic_axes
        u[1, :] .-= timestep .* (pressure[1, :] .- pressure[end, :]) ./ dx(domain)
        u[end, :] .= u[1, :]
    end
    if :y in domain.periodic_axes
        v[:, 1] .-= timestep .* (pressure[:, 1] .- pressure[:, end]) ./ dy(domain)
        v[:, end] .= v[:, 1]
    end
    apply_domain_boundaries!(u, v, domain, freestream; channel_walls)
    enforce_solid_faces!(u, v, solid, wall_velocity)
    return iterations, converged
end

function _helmholtz_diagonal(field::AbstractMatrix{T}, domain::DomainSpec{2,T}, ax::T, ay::T) where {T}
    diagonal = Matrix{T}(undef, size(field))
    periodic_x = :x in domain.periodic_axes && size(field, 1) == nx(domain)
    periodic_y = :y in domain.periodic_axes && size(field, 2) == ny(domain)
    for j in axes(field, 2), i in axes(field, 1)
        neighbors_x = (i > 1 || periodic_x ? 1 : 0) + (i < size(field, 1) || periodic_x ? 1 : 0)
        neighbors_y = (j > 1 || periodic_y ? 1 : 0) + (j < size(field, 2) || periodic_y ? 1 : 0)
        diagonal[i, j] = one(T) + T(neighbors_x) * ax + T(neighbors_y) * ay
    end
    return diagonal
end

function implicit_diffuse_scalar(
    field::AbstractMatrix{T},
    viscosity::T,
    timestep::T,
    domain::DomainSpec{2,T};
    tolerance::Real = 1.0e-5,
    max_iterations::Int = 80,
) where {T<:AbstractFloat}
    viscosity <= zero(T) && return Matrix{T}(field), 0, true
    ax = viscosity * timestep / dx(domain)^2
    ay = viscosity * timestep / dy(domain)^2
    diagonal = _helmholtz_diagonal(field, domain, ax, ay)
    inverse_diagonal = inv.(diagonal)
    periodic_x = :x in domain.periodic_axes && size(field, 1) == nx(domain)
    periodic_y = :y in domain.periodic_axes && size(field, 2) == ny(domain)
    function apply_operator!(output, candidate)
        for j in axes(candidate, 2), i in axes(candidate, 1)
            value = diagonal[i, j] * candidate[i, j]
            left = i > 1 ? i - 1 : periodic_x ? size(candidate, 1) : 0
            right = i < size(candidate, 1) ? i + 1 : periodic_x ? 1 : 0
            lower = j > 1 ? j - 1 : periodic_y ? size(candidate, 2) : 0
            upper = j < size(candidate, 2) ? j + 1 : periodic_y ? 1 : 0
            left > 0 && (value -= ax * candidate[left, j])
            right > 0 && (value -= ax * candidate[right, j])
            lower > 0 && (value -= ay * candidate[i, lower])
            upper > 0 && (value -= ay * candidate[i, upper])
            output[i, j] = value
        end
        return nothing
    end
    return _pcg(
        apply_operator!,
        Matrix{T}(field),
        inverse_diagonal;
        tolerance = T(tolerance),
        max_iterations,
    )
end

function implicit_diffuse_velocity(
    velocity::AbstractArray{T,3},
    viscosity::T,
    timestep::T,
    domain::DomainSpec{2,T};
    tolerance::Real = 1.0e-5,
) where {T<:AbstractFloat}
    output = similar(velocity)
    iterations = 0
    converged = true
    for component in 1:2
        result, component_iterations, component_converged = implicit_diffuse_scalar(
            view(velocity, :, :, component),
            viscosity,
            timestep,
            domain;
            tolerance,
        )
        output[:, :, component] = result
        iterations = max(iterations, component_iterations)
        converged &= component_converged
    end
    return output, iterations, converged
end
