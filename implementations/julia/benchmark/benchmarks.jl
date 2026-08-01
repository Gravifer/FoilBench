using BenchmarkTools
using FoilBenchJulia

suite = BenchmarkGroup()
suite["pcg32"] = @benchmarkable next_uint32!(rng) setup = (rng = PCG32(0))

run(suite; verbose = true)
