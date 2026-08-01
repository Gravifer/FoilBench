using FoilBenchJulia
using JSON3
using StaticArrays
using Test

const REPOSITORY_ROOT = normpath(joinpath(@__DIR__, "..", "..", ".."))
const FIXTURES = joinpath(REPOSITORY_ROOT, "spec", "conformance")

function rows_to_matrix(rows)
    isempty(rows) && return Matrix{Float64}(undef, 0, 0)
    width = length(first(rows))
    all(row -> length(row) == width, rows) || throw(DimensionMismatch("ragged matrix fixture"))
    return reduce(vcat, (permutedims(Float64.(row)) for row in rows))
end

@testset "PCG32 shared vectors" begin
    document = JSON3.read(read(joinpath(FIXTURES, "pcg32.json"), String))
    for case in document.cases
        rng = PCG32(case.seed, case.stream)
        @test [next_uint32!(rng) for _ in case.uint32] == UInt32.(case.uint32)
        float_rng = PCG32(case.seed, case.stream)
        bits = [reinterpret(UInt32, next_float32!(float_rng)) for _ in case.float32_bits]
        expected_bits = [parse(UInt32, String(value[3:end]); base = 16) for value in case.float32_bits]
        @test bits == expected_bits
    end
end

@testset "NACA 2412 shared vectors" begin
    document = JSON3.read(read(joinpath(FIXTURES, "naca2412.json"), String))
    foil = NacaFoil(
        FoilSpec(
            String(document.foil.naca),
            Float64(document.foil.chord),
            SVector{2,Float64}(document.foil.pivot),
        ),
    )
    x = Float64.(document.surface_x)
    upper, lower = surfaces(foil, x)
    @test upper ≈ Float64.(document.surface_upper) atol = document.absolute_tolerances.surface
    @test lower ≈ Float64.(document.surface_lower) atol = document.absolute_tolerances.surface
    float32_foil = NacaFoil(FoilSpec("2412", 1.0f0, SVector{2,Float32}(0.1, -0.2)))
    float32_upper, float32_lower = surfaces(float32_foil, Float32.(x))
    @test eltype(float32_upper) == Float32
    @test eltype(float32_lower) == Float32
    for query in document.queries
        points = rows_to_matrix(query.points)
        distance = signed_distance(foil, points, query.angle_degrees)
        @test distance ≈ Float64.(query.signed_distance) atol = document.absolute_tolerances.signed_distance
        @test normals(foil, points, query.angle_degrees) ≈
              rows_to_matrix(query.normals) atol = document.absolute_tolerances.normal
        @test (distance .<= 0) == Bool.(query.contains)
    end
end

@testset "Canonical state shared artifact" begin
    state = load_canonical_state(joinpath(FIXTURES, "canonical-state-f32"))
    @test state.source_language == "conformance"
    @test state.source_solver == "golden"
    @test state.bounds == ((-1.0f0, 1.0f0), (-0.75f0, 0.75f0))
    @test state.resolution == (4, 3)
    @test dimension(state) == 2
    @test scalar_type(state) == Float32
    @test size(state.velocity) == (1, 3, 4, 2)
    @test eltype(state.velocity) == Float32
    @test state.velocity[1, 1, 1, :] == Float32[-8 / 7, -1]
    @test state.density !== nothing
    @test size(state.density) == (1, 3, 4)

    mktempdir() do directory
        save_canonical_state(state, directory)
        roundtrip = load_canonical_state(directory)
        @test roundtrip.velocity == state.velocity
        @test roundtrip.density == state.density
        @test roundtrip.source_solver == state.source_solver
    end
end

@testset "Shared scenario loading" begin
    scenario = load_scenario(joinpath(REPOSITORY_ROOT, "scenarios", "airfoil", "default.json"))
    @test scenario.id == "naca2412-dynamic"
    @test scenario.domain.resolution == (160, 96)
    @test scenario.foil.naca == "2412"
    @test scenario.precision == :float32
    @test scenario.reynolds == 1000
    @test dimension(scenario) == 2
    @test scalar_type(scenario) == Float32
    midpoint = control_at(scenario, 3.0)
    @test midpoint.time == 3.0f0
    @test isfinite(midpoint.angle_degrees)
    @test isfinite(midpoint.angular_velocity_degrees)
end

@testset "Solver capabilities" begin
    scenario = load_scenario(joinpath(REPOSITORY_ROOT, "scenarios", "airfoil", "default.json"))
    info = SolverInfo("test", "Test", (2,), true, :cpu)
    @test supports(info, scenario)
    @test isnothing(require_supported(info, scenario))

    domain3 = DomainSpec(
        ((-1.0f0, 1.0f0), (-1.0f0, 1.0f0), (-0.1f0, 0.1f0)),
        (16, 16, 4),
        (:z,),
    )
    scenario3 = Scenario(
        1,
        "thin-3d",
        domain3,
        500.0f0,
        SVector{3,Float32}(1, 0, 0),
        FoilSpec("0012", 1.0f0, SVector{3,Float32}(0, 0, 0)),
        [ControlKeyframe(0.0f0, 0.0f0)],
        1.0f0,
        0.01f0,
        :float32,
        UInt64(0),
        Dict{String,Any}(),
    )
    @test !supports(info, scenario3)
    @test_throws ArgumentError require_supported(info, scenario3)
end
