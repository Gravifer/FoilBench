const D2Q9_C = SMatrix{2,9,Int8}(
    Int8[
        0 1 0 -1 0 1 -1 -1 1
        0 0 1 0 -1 1 1 -1 -1
    ],
)
const D2Q9_W = SVector{9,Float64}(4 / 9, 1 / 9, 1 / 9, 1 / 9, 1 / 9, 1 / 36, 1 / 36, 1 / 36, 1 / 36)
const D2Q9_OPPOSITE = SVector{9,Int8}(1, 4, 5, 2, 3, 8, 9, 6, 7)

struct LBMScaling{T<:AbstractFloat}
    lattice_dt::T
    lattice_speed::T
    viscosity::T
    effective_reynolds::T
    omega_plus::T
    omega_minus::T
    clamped::Bool
end

function lbm_scaling(
    scenario::Scenario{2,T},
    selected_reynolds::Real = scenario.reynolds,
) where {T}
    isfinite(selected_reynolds) && selected_reynolds > 0 ||
        throw(ArgumentError("Reynolds number must be finite and positive"))
    speed = reference_speed(scenario)
    reference_substeps = max(1, ceil(Int, scenario.output_dt * speed / (T(0.08) * dx(scenario.domain))))
    lattice_dt = scenario.output_dt / T(reference_substeps)
    lattice_speed = speed * lattice_dt / dx(scenario.domain)
    chord_cells = scenario.foil.chord / dx(scenario.domain)
    requested_viscosity = lattice_speed * chord_cells / T(selected_reynolds)
    minimum_viscosity = (T(0.52) - T(0.5)) / T(3)
    viscosity = max(requested_viscosity, minimum_viscosity)
    clamped = viscosity > requested_viscosity
    effective_reynolds = lattice_speed * chord_cells / viscosity
    tau_plus = T(0.5) + T(3) * viscosity
    tau_minus = T(0.5) + T(3 / 16) / max(tau_plus - T(0.5), T(1.0e-6))
    return LBMScaling(
        lattice_dt,
        lattice_speed,
        viscosity,
        effective_reynolds,
        inv(tau_plus),
        inv(tau_minus),
        clamped,
    )
end

"""Construct direction-major D2Q9 equilibrium populations from x-major fields."""
function lbm_equilibrium(
    density::AbstractMatrix{T},
    velocity::AbstractArray{T,3},
) where {T<:AbstractFloat}
    size(velocity) == (size(density, 1), size(density, 2), 2) ||
        throw(DimensionMismatch("LBM velocity and density shapes do not agree"))
    populations = Array{T,3}(undef, 9, size(density, 1), size(density, 2))
    for j in axes(density, 2), i in axes(density, 1)
        ux = velocity[i, j, 1]
        uy = velocity[i, j, 2]
        speed_squared = ux * ux + uy * uy
        rho = density[i, j]
        for direction in 1:9
            projection = T(D2Q9_C[1, direction]) * ux + T(D2Q9_C[2, direction]) * uy
            populations[direction, i, j] = rho * T(D2Q9_W[direction]) *
                (one(T) + T(3) * projection + T(4.5) * projection^2 - T(1.5) * speed_squared)
        end
    end
    return populations
end

"""Recover density and lattice velocity from direction-major D2Q9 populations."""
function lbm_macroscopic(populations::AbstractArray{T,3}) where {T<:AbstractFloat}
    size(populations, 1) == 9 || throw(DimensionMismatch("D2Q9 populations need nine directions"))
    density = Matrix{T}(undef, size(populations, 2), size(populations, 3))
    velocity = Array{T,3}(undef, size(populations, 2), size(populations, 3), 2)
    for j in axes(density, 2), i in axes(density, 1)
        rho = zero(T)
        momentum_x = zero(T)
        momentum_y = zero(T)
        for direction in 1:9
            population = populations[direction, i, j]
            rho += population
            momentum_x += population * T(D2Q9_C[1, direction])
            momentum_y += population * T(D2Q9_C[2, direction])
        end
        density[i, j] = rho
        denominator = max(rho, T(1.0e-12))
        velocity[i, j, 1] = momentum_x / denominator
        velocity[i, j, 2] = momentum_y / denominator
    end
    return density, velocity
end

"""Apply a two-relaxation-time collision while conserving local moments."""
function lbm_trt_collision(
    populations::AbstractArray{T,3},
    omega_plus::Real,
    omega_minus::Real,
) where {T<:AbstractFloat}
    density, velocity = lbm_macroscopic(populations)
    equilibrium = lbm_equilibrium(density, velocity)
    post = similar(populations)
    sum_rate = T(0.5) * (T(omega_plus) + T(omega_minus))
    difference_rate = T(0.5) * (T(omega_plus) - T(omega_minus))
    for j in axes(density, 2), i in axes(density, 1), direction in 1:9
        opposite = Int(D2Q9_OPPOSITE[direction])
        delta = populations[direction, i, j] - equilibrium[direction, i, j]
        opposite_delta = populations[opposite, i, j] - equilibrium[opposite, i, j]
        post[direction, i, j] = populations[direction, i, j] -
            sum_rate * delta - difference_rate * opposite_delta
    end
    return density, post
end
