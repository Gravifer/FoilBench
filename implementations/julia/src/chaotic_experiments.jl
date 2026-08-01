struct WakeSweepCase
    reynolds::Float64
    angle_degrees::Float64
    resolution::NTuple{2,Int}
end

function chaotic_scenario(
    base::Scenario{2,T},
    selected::WakeSweepCase,
    duration::Real,
) where {T}
    domain = DomainSpec(base.domain.bounds, selected.resolution, base.domain.periodic_axes)
    options = copy(base.solver_options)
    options["stable_advection"] = "skew-rk2"
    return Scenario(
        base.schema_version,
        "chaotic-wake-re$(selected.reynolds)-a$(selected.angle_degrees)-$(selected.resolution[1])x$(selected.resolution[2])",
        domain,
        T(selected.reynolds),
        base.freestream,
        base.foil,
        [ControlKeyframe(zero(T), T(selected.angle_degrees)),
            ControlKeyframe(T(duration), T(selected.angle_degrees))],
        T(duration),
        base.output_dt,
        base.precision,
        base.seed,
        options,
    )
end

function temporal_spectral_statistics(samples::AbstractVector{<:Real})
    count_value = length(samples)
    count_value >= 4 || return (entropy = 0.0, dominant_power_fraction = 0.0,
        broadband_power_fraction = 0.0)
    mean_value = sum(samples) / count_value
    powers = zeros(Float64, count_value ÷ 2 + 1)
    for frequency in 1:(length(powers) - 1)
        real_part = 0.0
        imaginary_part = 0.0
        for index in 1:count_value
            window = 0.5 - 0.5 * cos(2pi * (index - 1) / (count_value - 1))
            value = (Float64(samples[index]) - mean_value) * window
            angle = -2pi * frequency * (index - 1) / count_value
            real_part += value * cos(angle)
            imaginary_part += value * sin(angle)
        end
        powers[frequency + 1] = real_part^2 + imaginary_part^2
    end
    total = sum(powers)
    total > eps(Float64) || return (entropy = 0.0, dominant_power_fraction = 0.0,
        broadband_power_fraction = 0.0)
    probabilities = powers ./ total
    entropy = -sum(value > 0 ? value * log(value) : 0.0 for value in probabilities) /
        log(max(2, length(probabilities) - 1))
    dominant = argmax(powers)
    coherent = sum(view(powers, max(2, dominant - 1):min(length(powers), dominant + 1))) / total
    return (
        entropy = entropy,
        dominant_power_fraction = powers[dominant] / total,
        broadband_power_fraction = 1 - coherent,
    )
end

function _decorrelation_time(samples::Vector{Float64}, timestep::Float64)
    isempty(samples) && return 0.0
    mean_value = sum(samples) / length(samples)
    centered = samples .- mean_value
    variance = sum(abs2, centered)
    variance > eps(Float64) || return 0.0
    for lag in 0:(length(samples) - 1)
        overlap = length(samples) - lag
        correlation = sum(centered[index] * centered[index + lag] for index in 1:overlap) /
            overlap
        normalized = correlation / (variance / length(samples))
        normalized < exp(-1) && return lag * timestep
    end
    return (length(samples) - 1) * timestep
end

function _small_scale_vorticity_fraction(omega::AbstractMatrix{T}, domain::DomainSpec{2,T}) where {T}
    total = sum(abs2, omega)
    total > eps(T) || return 0.0
    gradient_energy = zero(T)
    for j in 2:(ny(domain) - 1), i in 2:(nx(domain) - 1)
        gradient_x = T(0.5) * (omega[i + 1, j] - omega[i - 1, j])
        gradient_y = T(0.5) * (omega[i, j + 1] - omega[i, j - 1])
        gradient_energy += gradient_x^2 + gradient_y^2
    end
    return Float64(gradient_energy / total)
end

function run_chaotic_wake_case(
    base::Scenario{2,T},
    selected::WakeSweepCase;
    duration::Real = 12,
    burn_in::Real = 4,
) where {T}
    0 <= burn_in < duration || throw(ArgumentError("burn-in must lie inside the run"))
    scenario = chaotic_scenario(base, selected, duration)
    solver = StableFluidsSolver(T)
    initialize!(solver, scenario, NacaFoil(scenario.foil), scenario.seed)
    probe = reshape(T[scenario.foil.pivot[1] + T(1.5) * scenario.foil.chord,
        scenario.foil.pivot[2]], 2, 1)
    transverse = Float64[]
    enstrophy_values = Float64[]
    maximum_speed = 0.0
    simulated = zero(T)
    started = time_ns()
    while simulated < scenario.duration - T(1.0e-12)
        timestep = min(scenario.output_dt, scenario.duration - simulated)
        simulated += timestep
        report = advance!(solver, control_at(scenario, simulated), timestep)
        maximum_speed = max(maximum_speed, Float64(report.max_speed))
        if simulated >= T(burn_in)
            push!(transverse, Float64(sample_velocity(solver, probe)[2, 1]))
            push!(enstrophy_values, diagnostics(solver).values["enstrophy"])
        end
    end
    wall_seconds = (time_ns() - started) / 1.0e9
    spectrum = temporal_spectral_statistics(transverse)
    transverse_mean = isempty(transverse) ? 0.0 : sum(transverse) / length(transverse)
    probe_rms = isempty(transverse) ? 0.0 :
        sqrt(sum((value - transverse_mean)^2 for value in transverse) / length(transverse))
    enstrophy_mean = isempty(enstrophy_values) ? 0.0 : sum(enstrophy_values) / length(enstrophy_values)
    enstrophy_std = isempty(enstrophy_values) ? 0.0 :
        sqrt(sum((value - enstrophy_mean)^2 for value in enstrophy_values) / length(enstrophy_values))
    omega = vorticity(cell_velocity(solver), scenario.domain)
    return Dict{String,Any}(
        "reynolds" => selected.reynolds,
        "angle_degrees" => selected.angle_degrees,
        "resolution" => collect(selected.resolution),
        "duration" => Float64(duration),
        "analysis_duration" => Float64(duration - burn_in),
        "wall_seconds" => wall_seconds,
        "probe_rms" => probe_rms,
        "spectral_entropy" => spectrum.entropy,
        "dominant_power_fraction" => spectrum.dominant_power_fraction,
        "broadband_power_fraction" => spectrum.broadband_power_fraction,
        "decorrelation_time" => _decorrelation_time(transverse, Float64(scenario.output_dt)),
        "enstrophy_mean" => enstrophy_mean,
        "enstrophy_coefficient_of_variation" => enstrophy_std / max(enstrophy_mean, eps(Float64)),
        "maximum_speed" => maximum_speed,
        "vorticity_small_scale_fraction" => _small_scale_vorticity_fraction(omega, scenario.domain),
    )
end

function _wake_rms_difference(
    first::AbstractArray{T,3},
    second::AbstractArray{T,3},
    mask::AbstractMatrix{Bool},
) where {T}
    total = zero(T)
    samples = 0
    for component in 1:2, j in axes(mask, 2), i in axes(mask, 1)
        mask[i, j] || continue
        total += (first[i, j, component] - second[i, j, component])^2
        samples += 1
    end
    return sqrt(total / max(samples, 1))
end

function _fit_exponential(times::Vector{Float64}, differences::Vector{Float64}, initial::Float64)
    selected = [index for index in eachindex(times) if
        differences[index] >= 1.5 * initial && differences[index] <= 0.02 && isfinite(differences[index])]
    length(selected) >= 8 || return (0.0, 0.0, length(selected))
    x = times[selected]
    y = log.(differences[selected])
    x_mean = sum(x) / length(x)
    y_mean = sum(y) / length(y)
    denominator = sum((value - x_mean)^2 for value in x)
    slope = sum((x[index] - x_mean) * (y[index] - y_mean) for index in eachindex(x)) /
        max(denominator, eps(Float64))
    intercept = y_mean - slope * x_mean
    residual = sum((y[index] - (intercept + slope * x[index]))^2 for index in eachindex(x))
    total = sum((value - y_mean)^2 for value in y)
    return slope, 1 - residual / max(total, eps(Float64)), length(selected)
end

function run_chaos_sensitivity(
    base::Scenario{2,T},
    selected::WakeSweepCase;
    duration::Real = 12,
    epsilon::Real = 1.0e-4,
) where {T}
    scenario = chaotic_scenario(base, selected, duration)
    geometry = NacaFoil(scenario.foil)
    reference = StableFluidsSolver(T)
    perturbed = StableFluidsSolver(T)
    initialize!(reference, scenario, geometry, scenario.seed)
    initialize!(perturbed, scenario, geometry, scenario.seed)
    initial_control = control_at(scenario, zero(T))
    reference_state = export_state(reference)
    import_state!(reference, reference_state, initial_control)
    state = export_state(perturbed)
    velocity = canonical_to_cell(state)
    centers = cell_centers(scenario.domain)
    streamfunction = Matrix{T}(undef, nx(scenario.domain), ny(scenario.domain))
    x0 = scenario.domain.bounds[1][1]
    y0 = scenario.domain.bounds[2][1]
    for j in 1:ny(scenario.domain), i in 1:nx(scenario.domain)
        x = centers[i, j, 1]
        y = centers[i, j, 2]
        streamfunction[i, j] = exp(-((x - T(0.2)) / T(0.8))^2 - ((y - T(0.25)) / T(0.5))^2) *
            sin(T(2pi) * (x - x0) / T(1.3)) * sin(T(2pi) * (y - y0) / T(0.9))
    end
    perturbation = zeros(T, nx(scenario.domain), ny(scenario.domain), 2)
    for j in 1:ny(scenario.domain), i in 1:nx(scenario.domain)
        im, ip = max(i - 1, 1), min(i + 1, nx(scenario.domain))
        jm, jp = max(j - 1, 1), min(j + 1, ny(scenario.domain))
        perturbation[i, j, 1] = (streamfunction[i, jp] - streamfunction[i, jm]) /
            (T(jp - jm) * dy(scenario.domain))
        perturbation[i, j, 2] = -(streamfunction[ip, j] - streamfunction[im, j]) /
            (T(ip - im) * dx(scenario.domain))
    end
    solid = solid_mask(geometry, scenario.domain, selected.angle_degrees)
    perturbation[repeat(solid, 1, 1, 2)] .= zero(T)
    maximum_perturbation = maximum(hypot(perturbation[i, j, 1], perturbation[i, j, 2]) for
        i in 1:nx(scenario.domain), j in 1:ny(scenario.domain))
    perturbation .*= T(epsilon) / max(maximum_perturbation, eps(T))
    velocity .+= perturbation
    perturbed_state = CanonicalFlowState(
        state.schema_version,
        state.bounds,
        state.resolution,
        state.periodic_axes,
        state.time,
        state.angle_degrees,
        state.angular_velocity_degrees,
        state.source_language,
        "deterministic-perturbation",
        cell_to_canonical(velocity),
        state.density,
    )
    import_state!(perturbed, perturbed_state, initial_control)
    wake = falses(nx(scenario.domain), ny(scenario.domain))
    for j in 1:ny(scenario.domain), i in 1:nx(scenario.domain)
        wake[i, j] = centers[i, j, 1] > scenario.foil.pivot[1] && !solid[i, j]
    end
    initial_difference = Float64(_wake_rms_difference(cell_velocity(perturbed), cell_velocity(reference), wake))
    times = Float64[]
    differences = Float64[]
    simulated = zero(T)
    started = time_ns()
    while simulated < scenario.duration - T(1.0e-12)
        timestep = min(scenario.output_dt, scenario.duration - simulated)
        simulated += timestep
        control = control_at(scenario, simulated)
        advance!(reference, control, timestep)
        advance!(perturbed, control, timestep)
        push!(times, Float64(simulated))
        push!(differences, Float64(_wake_rms_difference(
            cell_velocity(perturbed), cell_velocity(reference), wake)))
    end
    exponent, r_squared, samples = _fit_exponential(times, differences, initial_difference)
    maximum_difference = maximum(differences)
    return Dict{String,Any}(
        "scenario" => scenario.id,
        "epsilon" => Float64(epsilon),
        "initial_wake_rms_difference" => initial_difference,
        "final_wake_rms_difference" => last(differences),
        "maximum_wake_rms_difference" => maximum_difference,
        "amplification" => maximum_difference / max(initial_difference, eps(Float64)),
        "finite_time_exponent" => exponent,
        "exponential_fit_r_squared" => r_squared,
        "exponential_fit_samples" => samples,
        "wall_seconds" => (time_ns() - started) / 1.0e9,
        "times" => times,
        "wake_rms_differences" => differences,
    )
end
