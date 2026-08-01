using BenchmarkTools
using FoilBenchJulia
using StaticArrays

suite = BenchmarkGroup()
suite["pcg32"] = @benchmarkable next_uint32!(rng) setup = (rng = PCG32(0))

domain = DomainSpec(((-1.0f0, 1.0f0), (-0.75f0, 0.75f0)), (64, 48), ())
density = ones(Float32, 64, 48)
lattice_velocity = zeros(Float32, 64, 48, 2)
populations = lbm_equilibrium(density, lattice_velocity)
positions = zeros(Float32, 2, 4 * 64 * 48)
particle_velocity = zeros(Float32, size(positions))
for particle in axes(positions, 2)
    positions[1, particle] = -1 + 2 * ((particle - 1) % 64 + 0.5f0) / 64
    positions[2, particle] = -0.75f0 + 1.5f0 * (((particle - 1) ÷ 64) % 48 + 0.5f0) / 48
end

suite["lbm"] = BenchmarkGroup()
suite["lbm"]["collision"] = @benchmarkable lbm_trt_collision($populations, 1.2f0, 0.9f0)
suite["pic"] = BenchmarkGroup()
suite["pic"]["scatter"] = @benchmarkable particle_to_grid(
    $positions,
    $particle_velocity,
    $domain,
    $(SVector{2,Float32}(1, 0)),
)
grid = particle_to_grid(positions, particle_velocity, domain, SVector{2,Float32}(1, 0))
suite["pic"]["gather"] = @benchmarkable grid_to_particle($grid, $positions, $domain)

run(suite; verbose = true)
