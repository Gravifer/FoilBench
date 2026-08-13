using FoilBenchJulia

struct FailingViewerCommand <: FoilBenchJulia.ViewerCommand end
FoilBenchJulia._apply_command!(::ViewerWorker, ::FailingViewerCommand) =
    throw(ArgumentError("injected command bug"))

struct FailingStepSolver{T<:AbstractFloat} <: FoilBenchJulia.AbstractFlowSolver{2,T}
    inner::FoilBenchJulia.AbstractFlowSolver{2,T}
end

FoilBenchJulia.solver_info(solver::FailingStepSolver) = solver_info(solver.inner)
FoilBenchJulia.reynolds(solver::FailingStepSolver) = reynolds(solver.inner)
FoilBenchJulia.advance!(::FailingStepSolver, ::ControlState, ::Real) =
    throw(NumericalFailure(:excessive_velocity, "injected post-import failure"))

struct PresentationFailingSolver{T<:AbstractFloat} <: FoilBenchJulia.AbstractFlowSolver{2,T}
    inner::FoilBenchJulia.AbstractFlowSolver{2,T}
    failure::Symbol
end

struct RotationTracerSolver{T<:AbstractFloat} <: FoilBenchJulia.AbstractFlowSolver{2,T} end

function FoilBenchJulia.sample_velocity(
    ::RotationTracerSolver{T},
    points::AbstractMatrix{T},
) where {T}
    sampled = similar(points)
    sampled[1, :] .= .-points[2, :]
    sampled[2, :] .= points[1, :]
    return sampled
end

FoilBenchJulia.solver_info(solver::PresentationFailingSolver) = solver_info(solver.inner)
FoilBenchJulia.reynolds(solver::PresentationFailingSolver) = reynolds(solver.inner)
FoilBenchJulia.state_revision(solver::PresentationFailingSolver) = state_revision(solver.inner)
FoilBenchJulia.advance!(solver::PresentationFailingSolver, control::ControlState, target_dt::Real) =
    advance!(solver.inner, control, target_dt)
FoilBenchJulia.sample_velocity(solver::PresentationFailingSolver, points::AbstractMatrix) =
    solver.failure == :tracer ?
    throw(NumericalFailure(:nonfinite_state, "injected tracer failure")) :
    sample_velocity(solver.inner, points)
FoilBenchJulia.diagnostics(solver::PresentationFailingSolver) =
    solver.failure == :diagnostic ?
    throw(NumericalFailure(:nonfinite_state, "injected diagnostic failure")) :
    diagnostics(solver.inner)
using JSON3
using StaticArrays
using Test

@testset "Canonical state v2 identity round trip" begin
    geometry = FoilSpec("2412", 1.0f0, SVector{2,Float32}(0, 0))
    state = CanonicalFlowState(
        2, ((0.0f0, 2.0f0), (-1.0f0, 1.0f0)), (8, 4), (), 0.25f0,
        14.0f0, 0.0f0, "rust", "stable-fluids", zeros(Float32, 1, 4, 8, 2),
        nothing, geometry, "native",
    )
    mktempdir() do directory
        save_canonical_state(state, directory)
        roundtrip = load_canonical_state(directory)
        @test roundtrip.schema_version == 2
        @test roundtrip.geometry == geometry
        @test roundtrip.source_language == "rust"
        @test roundtrip.producer_execution_target == "native"
    end
end

const REPOSITORY_ROOT = normpath(joinpath(@__DIR__, "..", "..", ".."))
const FIXTURES = joinpath(REPOSITORY_ROOT, "spec", "conformance")
const REVISION5_SCHEMAS = joinpath(REPOSITORY_ROOT, "spec", "schemas")

function rows_to_matrix(rows)
    isempty(rows) && return Matrix{Float64}(undef, 0, 0)
    width = length(first(rows))
    all(row -> length(row) == width, rows) || throw(DimensionMismatch("ragged matrix fixture"))
    return reduce(vcat, (permutedims(Float64.(row)) for row in rows))
end

function resized_scenario(scenario::Scenario{D,T}, resolution::NTuple{D,Int}) where {D,T}
    domain = DomainSpec(scenario.domain.bounds, resolution, scenario.domain.periodic_axes)
    return Scenario(
        scenario.schema_version,
        scenario.id,
        domain,
        scenario.reynolds,
        scenario.freestream,
        scenario.foil,
        scenario.controls,
        scenario.duration,
        scenario.output_dt,
        scenario.precision,
        scenario.seed,
        copy(scenario.solver_options),
    )
end

function scenario_with_output_dt(scenario::Scenario{D,T}, output_dt::Real) where {D,T}
    return Scenario(
        scenario.schema_version,
        scenario.id,
        scenario.domain,
        scenario.reynolds,
        scenario.freestream,
        scenario.foil,
        scenario.controls,
        scenario.duration,
        T(output_dt),
        scenario.precision,
        scenario.seed,
        copy(scenario.solver_options),
    )
end

function wait_for_snapshot(
    worker::ViewerWorker;
    after_revision::Integer = 0,
    timeout::Float64 = 10.0,
)
    return wait_for_revision(worker, after_revision + 1; timeout)
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

function set_fixture_path!(document, path, value)
    cursor = document
    for segment in path[1:(end - 1)]
        cursor = cursor[segment isa Integer ? Int(segment) + 1 : String(segment)]
    end
    final = last(path)
    cursor[final isa Integer ? Int(final) + 1 : String(final)] = value
    return document
end

@testset "Revision 5 conformance fixtures" begin
    geometry_document = JSON3.read(
        read(joinpath(FIXTURES, "geometry-v1.json"), String),
    )
    descriptor = geometry_document.descriptor
    foil = NacaFoil(FoilSpec(
        String(descriptor.naca),
        Float64(descriptor.chord),
        SVector{2,Float64}(descriptor.pivot),
    ))
    x = Float64.(geometry_document.surface_x)
    upper, lower = surfaces(foil, x)
    tolerances = geometry_document.absolute_tolerances
    @test upper ≈ Float64.(geometry_document.surface_upper) atol = tolerances.surface
    @test lower ≈ Float64.(geometry_document.surface_lower) atol = tolerances.surface
    points = rows_to_matrix(geometry_document.points)
    angle = Float64(geometry_document.angle_degrees)
    @test signed_distance(foil, points, angle) ≈
          Float64.(geometry_document.signed_distance) atol = tolerances.signed_distance
    @test normals(foil, points, angle) ≈
          rows_to_matrix(geometry_document.normals) atol = tolerances.normal
    @test foil_contains(foil, points, angle) == Bool.(geometry_document.contains)
    control = ControlState(
        0.0,
        angle,
        Float64(geometry_document.angular_velocity_degrees),
    )
    @test wall_velocity(foil, points, control) ≈
          rows_to_matrix(geometry_document.wall_velocity) atol = tolerances.wall_velocity
    @test maximum_radius(foil) ≈
          Float64(geometry_document.maximum_radius) atol = tolerances.radius

    manifest = JSON3.read(
        read(joinpath(FIXTURES, "canonical-manifest-v2.json"), String),
        Dict{String,Any},
    )
    FoilBenchJulia.validate_json_file(
        manifest,
        joinpath(REVISION5_SCHEMAS, "canonical-manifest-v2.schema.json"),
    )
    @test manifest["geometry"]["family"] == "naca-four-digit-v1"
    @test manifest["producer"]["implementation"] == "rust"
    @test manifest["producer"]["execution_target"] == "native"

    fidelity = JSON3.read(
        read(joinpath(FIXTURES, "fidelity-cases.json"), String),
    )
    for case in fidelity.cases
        scenario = load_scenario(joinpath(REPOSITORY_ROOT, String(case.scenario)))
        @test length(case.resolution) == dimension(scenario)
        @test case.duration > 0
        @test !isempty(propertynames(case.metrics))
    end

    mac = JSON3.read(read(joinpath(FIXTURES, "mac-boundary.json"), String))
    @test mac.periodic_duplicate == "endpoint-average"
    domain = DomainSpec(((0.0, 2.0), (-1.0, 1.0)), (8, 6))
    u = fill(-3.0, 9, 6)
    v = fill(-4.0, 8, 7)
    apply_domain_boundaries!(u, v, domain, SVector(1.25, -0.5))
    @test u[1, :] == fill(1.25, 6)
    @test u[:, 1] == fill(1.25, 9)
    @test u[:, end] == fill(1.25, 9)
    @test v[:, 1] == fill(-0.5, 8)
    @test v[:, end] == fill(-0.5, 8)

    lbm = JSON3.read(read(joinpath(FIXTURES, "lbm-boundary.json"), String))
    default = load_scenario(joinpath(REPOSITORY_ROOT, "scenarios", "airfoil", "default.json"))
    preview = resized_scenario(default, (160, 96))
    sponge = FoilBenchJulia._lbm_sponge(preview)
    @test sponge[81, 1] ≈ lbm.sponge.transverse_maximum
    @test sponge[end, 49] ≈ lbm.sponge.outlet_maximum
    @test sponge[end, 1] ≈ max(lbm.sponge.transverse_maximum, lbm.sponge.outlet_maximum)
    @test sponge[81, 49] == 0

    negative = JSON3.read(
        read(joinpath(FIXTURES, "scenario-negative.json"), String),
        Dict{String,Any},
    )
    scenario_schema = joinpath(REPOSITORY_ROOT, "spec", "schemas", "scenario.schema.json")
    for case in negative["cases"]
        document = JSON3.read(
            read(joinpath(REPOSITORY_ROOT, String(case["base"])), String),
            Dict{String,Any},
        )
        set_fixture_path!(document, case["path"], case["value"])
        rejected = false
        try
            FoilBenchJulia.validate_json_file(document, scenario_schema)
            selected_dimension = Int(document["dimension"])
            FoilBenchJulia._load_scenario(document, Val(selected_dimension))
        catch
            rejected = true
        end
        @test rejected
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
        written_manifest = JSON3.read(
            read(joinpath(directory, "manifest.json"), String),
            Dict{String,Any},
        )
        @test written_manifest["velocity"]["order"] == "F"
        @test written_manifest["density"]["order"] == "F"
        open(joinpath(directory, "velocity.npy"), "r") do io
            @test read(io, 6) == UInt8[0x93, 0x4e, 0x55, 0x4d, 0x50, 0x59]
            version = Tuple(read(io, 2))
            header_length_bytes = read(io, version[1] == 1 ? 2 : 4)
            header_length = sum(
                Int(byte) << (8 * (index - 1)) for
                (index, byte) in enumerate(header_length_bytes)
            )
            header = String(read(io, header_length))
            @test occursin("'fortran_order': True", header)
        end
        roundtrip = load_canonical_state(directory)
        @test roundtrip.velocity == state.velocity
        @test roundtrip.density == state.density
        @test roundtrip.source_solver == state.source_solver
    end
end

@testset "Canonical state shared Fortran artifact" begin
    state = load_canonical_state(joinpath(FIXTURES, "canonical-state-f32-fortran"))
    @test state.velocity[1, 3, 4, 2] ≈ Float32(15 / 7)
    @test state.density !== nothing
    @test state.density[1, 3, 4] ≈ 1.1f0
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

    source_document = JSON3.read(
        read(joinpath(REPOSITORY_ROOT, "scenarios", "airfoil", "default.json"), String),
        Dict{String,Any},
    )
    invalid_scenarios = Dict{String,Any}[]
    for mutation in (
        document -> (document["schema_version"] = 2),
        document -> (document["unexpected"] = true),
        document -> (document["precision"] = "float16"),
        document -> (document["periodic_axes"] = ["q"]),
        document -> (document["resolution"] = [3, 8]),
        document -> (document["resolution"] = [16.5, 8]),
        document -> (document["foil"]["unexpected"] = true),
    )
        selected = deepcopy(source_document)
        mutation(selected)
        push!(invalid_scenarios, selected)
    end
    for (index, invalid) in enumerate(invalid_scenarios)
        mktempdir() do directory
            path = joinpath(directory, "invalid-$index.json")
            open(path, "w") do io
                JSON3.pretty(io, invalid)
            end
            @test_throws ArgumentError load_scenario(path)
        end
    end
end

@testset "Solver capabilities" begin
    scenario = load_scenario(joinpath(REPOSITORY_ROOT, "scenarios", "airfoil", "default.json"))
    info = SolverInfo("test", "Test", (2,), true, (:float32, :float64), :cpu)
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

@testset "Interpolation and MAC grid" begin
    domain = DomainSpec(((0.0, 2.0), (-1.0, 1.0)), (8, 6), ())
    centers = cell_centers(domain)
    scalar = @. 2.0 * centers[:, :, 1] - 3.0 * centers[:, :, 2]
    points = Matrix{Float64}(undef, 2, 3)
    points[:, 1] = centers[3, 2, :]
    points[:, 2] = centers[5, 4, :]
    points[:, 3] = centers[7, 5, :]
    @test sample_scalar(scalar, points, domain) ≈
          [2.0 * points[1, index] - 3.0 * points[2, index] for index in axes(points, 2)]

    velocity = zeros(Float64, 8, 6, 2)
    velocity[:, :, 1] .= 1.25
    velocity[:, :, 2] .= -0.5
    sampled = sample_velocity_field(velocity, points, domain)
    @test sampled == repeat([1.25, -0.5], 1, 3)
    @test rk2_backtrace(velocity, points, 0.2, domain) ≈
          points .- [0.25, -0.1]

    u, v = cell_to_faces(velocity)
    @test size(u) == (9, 6)
    @test size(v) == (8, 7)
    @test faces_to_cell(u, v) == velocity
    @test face_divergence(u, v, domain) == zeros(8, 6)
    @test canonical_to_cell(
        CanonicalFlowState(
            1,
            domain.bounds,
            domain.resolution,
            domain.periodic_axes,
            0.0,
            0.0,
            0.0,
            "julia",
            "test",
            cell_to_canonical(velocity),
        ),
    ) == velocity
end

@testset "Geometry grids and metrics" begin
    domain = DomainSpec(((-1.0f0, 2.0f0), (-1.0f0, 1.0f0)), (48, 32), ())
    foil = NacaFoil(FoilSpec("0012", 1.0f0, SVector{2,Float32}(0, 0)))
    solid = solid_mask(foil, domain, 10.0f0)
    @test any(solid)
    @test !all(solid)
    control = ControlState(0.0f0, 10.0f0, 30.0f0)
    wall = wall_velocity_grid(foil, domain, control)
    @test size(wall) == (48, 32, 2)
    @test all(isfinite, wall)

    velocity = zeros(Float32, 48, 32, 2)
    velocity[:, :, 1] .= 1.0f0
    velocity[:, :, 2] .= 0.5f0
    @test kinetic_energy(velocity) ≈ 0.625f0
    @test momentum(velocity) ≈ SVector{2,Float32}(1, 0.5)
    @test enstrophy(velocity, domain) ≈ 0.0f0 atol = 1.0f-6
    @test divergence_l2(velocity, domain) ≈ 0.0f0 atol = 1.0f-6
    @test solid_leakage(velocity, falses(48, 32)) == 0.0f0
    @test wake_width(velocity, domain, 0.0f0) == 0.0f0
    @test recirculation_area(velocity, domain, 0.0f0) == 0.0f0

    wake_fixture = JSON3.read(
        read(joinpath(FIXTURES, "wake-metrics.json"), String),
    )
    wake_domain = DomainSpec(
        (
            Tuple(Float32.(wake_fixture.bounds[1])),
            Tuple(Float32.(wake_fixture.bounds[2])),
        ),
        Tuple(Int.(wake_fixture.resolution)),
        (),
    )
    wake_velocity = zeros(Float32, nx(wake_domain), ny(wake_domain), 2)
    wake_solid = falses(nx(wake_domain), ny(wake_domain))
    for j in 1:ny(wake_domain), i in 1:nx(wake_domain), component in 1:2
        wake_velocity[i, j, component] = wake_fixture.velocity[j][i][component]
        wake_solid[i, j] = wake_fixture.solid[j][i]
    end
    @test wake_width(
        wake_velocity,
        wake_domain,
        wake_fixture.pivot_x;
        chord = wake_fixture.chord,
        freestream_u = wake_fixture.freestream_u,
        solid = wake_solid,
    ) ≈ wake_fixture.expected.wake_width
    @test recirculation_area(
        wake_velocity,
        wake_domain,
        wake_fixture.pivot_x;
        solid = wake_solid,
    ) ≈ wake_fixture.expected.recirculation_area
end

@testset "Matrix-free projection and diffusion" begin
    domain = DomainSpec(((0.0, 2.0), (-1.0, 1.0)), (24, 16), ())
    u = zeros(Float64, 25, 16)
    v = zeros(Float64, 24, 17)
    for j in axes(u, 2), i in axes(u, 1)
        u[i, j] = 0.2 * sin(0.31 * i + 0.17 * j)
    end
    for j in axes(v, 2), i in axes(v, 1)
        v[i, j] = 0.2 * cos(0.23 * i - 0.19 * j)
    end
    solid = falses(24, 16)
    wall = zeros(Float64, 24, 16, 2)
    freestream = SVector{2,Float64}(0, 0)
    apply_domain_boundaries!(u, v, domain, freestream)
    before = sqrt(sum(abs2, face_divergence(u, v, domain)))
    iterations, residual, converged = project_faces!(
        u,
        v,
        domain,
        solid,
        wall,
        freestream,
        0.02;
        tolerance = 1.0e-7,
    )
    after = sqrt(sum(abs2, face_divergence(u, v, domain)))
    @test converged
    @test residual <= 1.0e-7
    @test iterations > 0
    @test after < 0.5 * before

    periodic_domain = DomainSpec(((0.0, 2.0), (0.0, 1.0)), (16, 12), (:x, :y))
    periodic_u = [0.1 * sin(0.31 * i + 0.17 * j) for i in 1:17, j in 1:12]
    periodic_v = [0.1 * cos(0.23 * i - 0.19 * j) for i in 1:16, j in 1:13]
    periodic_solid = falses(16, 12)
    periodic_wall = zeros(Float64, 16, 12, 2)
    apply_domain_boundaries!(periodic_u, periodic_v, periodic_domain, freestream)
    periodic_before = sqrt(sum(abs2, face_divergence(periodic_u, periodic_v, periodic_domain)))
    _, periodic_residual, periodic_converged = project_faces!(
        periodic_u,
        periodic_v,
        periodic_domain,
        periodic_solid,
        periodic_wall,
        freestream,
        0.01;
        tolerance = 1.0e-9,
    )
    periodic_after = sqrt(sum(abs2, face_divergence(periodic_u, periodic_v, periodic_domain)))
    @test periodic_converged
    @test periodic_residual <= 1.0e-8
    @test periodic_after < 1.0e-5 * periodic_before
    @test periodic_u[1, :] ≈ periodic_u[end, :]
    @test periodic_v[:, 1] ≈ periodic_v[:, end]

    impulse = zeros(Float64, 24, 16)
    impulse[12, 8] = 1.0
    diffused, diffusion_iterations, diffusion_residual, diffusion_converged = implicit_diffuse_scalar(
        impulse,
        0.1,
        0.02,
        domain;
        tolerance = 1.0e-8,
    )
    @test diffusion_converged
    @test diffusion_residual <= 1.0e-8
    @test diffusion_iterations > 0
    @test 0.0 < maximum(diffused) < 1.0
    @test sum(diffused) ≈ 1.0 atol = 1.0e-7

    periodic_faces = DomainSpec(((0.0, 2.0), (0.0, 1.0)), (32, 16), (:x, :y))
    phase = 2pi .* (0:32) ./ 32
    periodic_u = repeat(reshape(sin.(phase), :, 1), 1, 16)
    original_u = copy(periodic_u)
    viscosity = 0.2
    timestep = 0.03
    diffused_u, _, face_residual, face_converged = implicit_diffuse_scalar(
        periodic_u,
        viscosity,
        timestep,
        periodic_faces;
        tolerance = 1.0e-10,
    )
    eigenvalue = 4sin(pi / 32)^2 / dx(periodic_faces)^2
    decay = inv(1 + viscosity * timestep * eigenvalue)
    @test face_converged
    @test face_residual <= 1.0e-10
    @test diffused_u[1:32, :] ≈ decay .* original_u[1:32, :] atol = 1.0e-8
    @test diffused_u[end, :] ≈ diffused_u[1, :] atol = 1.0e-12

    nonfinite_rhs = zeros(Float64, 4, 4)
    nonfinite_rhs[2, 3] = Inf
    nonfinite_failure = try
        FoilBenchJulia._pcg(
            (output, direction) -> (output .= direction),
            nonfinite_rhs,
            ones(Float64, 4, 4);
            tolerance = 1.0e-6,
            max_iterations = 4,
        )
        nothing
    catch error
        error
    end
    @test nonfinite_failure isa NumericalFailure
    @test (nonfinite_failure::NumericalFailure).reason == :nonfinite_state

    negative_operator! = (output, direction) -> (output .= -direction)
    projection_failure = try
        FoilBenchJulia._pcg(
            negative_operator!,
            ones(Float64, 4, 4),
            ones(Float64, 4, 4);
            tolerance = 1.0e-6,
            max_iterations = 4,
        )
        nothing
    catch error
        error
    end
    @test projection_failure isa NumericalFailure
    @test (projection_failure::NumericalFailure).reason == :projection_failure
end

@testset "Stable Fluids advection operators" begin
    domain = DomainSpec(((0.0f0, 2.0f0), (-1.0f0, 1.0f0)), (20, 12), (:x, :y))
    velocity = zeros(Float32, 20, 12, 2)
    velocity[:, :, 1] .= 1.0f0
    velocity[:, :, 2] .= -0.25f0
    for maccormack in (false, true)
        advected = advect_velocity(velocity, 0.02f0, domain; maccormack)
        @test advected ≈ velocity atol = 1.0f-6
        u, v = cell_to_faces(velocity)
        advected_u, advected_v = advect_faces(u, v, 0.02f0, domain; maccormack)
        @test advected_u ≈ u atol = 1.0f-6
        @test advected_v ≈ v atol = 1.0f-6
    end

    u, v = cell_to_faces(velocity)
    solid = falses(20, 12)
    wall = zeros(Float32, 20, 12, 2)
    skew_u, skew_v = advect_faces_skew_rk2(
        u,
        v,
        0.02f0,
        domain,
        solid,
        wall,
        SVector{2,Float32}(1, -0.25),
    )
    @test skew_u ≈ u atol = 1.0f-6
    @test skew_v ≈ v atol = 1.0f-6

    cancellation_u = Matrix{Float32}(undef, 5, 4)
    for j in axes(cancellation_u, 2), i in axes(cancellation_u, 1)
        cancellation_u[i, j] = isodd(i) ? 2.0f0 : -2.0f0
    end
    cancellation_v = zeros(Float32, 4, 5)
    cancellation_domain = DomainSpec(((0.0f0, 2.0f0), (-1.0f0, 1.0f0)), (4, 4), ())
    @test faces_to_cell(cancellation_u, cancellation_v) ≈ zeros(Float32, 4, 4, 2)
    @test FoilBenchJulia.skew_face_advection_rate(
        cancellation_u, cancellation_v, cancellation_domain,
    ) ≈ 4.0f0

    phase_x = Float32.(2pi .* (0:domain.resolution[1]) ./ domain.resolution[1])
    periodic_u = repeat(reshape(sin.(phase_x), :, 1), 1, domain.resolution[2])
    derivative_u = FoilBenchJulia._derivative_x(
        periodic_u, dx(domain), true; duplicate_endpoint = true,
    )
    expected_x = sin(Float32(2pi / domain.resolution[1])) / dx(domain)
    @test derivative_u[1, :] ≈ fill(expected_x, domain.resolution[2]) atol = 1.0f-6
    @test derivative_u[end, :] ≈ derivative_u[1, :] atol = 1.0f-6

    phase_y = Float32.(2pi .* (0:domain.resolution[2]) ./ domain.resolution[2])
    periodic_v = repeat(reshape(sin.(phase_y), 1, :), domain.resolution[1], 1)
    derivative_v = FoilBenchJulia._derivative_y(
        periodic_v, dy(domain), true; duplicate_endpoint = true,
    )
    expected_y = sin(Float32(2pi / domain.resolution[2])) / dy(domain)
    @test derivative_v[:, 1] ≈ fill(expected_y, domain.resolution[1]) atol = 5.0f-6
    @test derivative_v[:, end] ≈ derivative_v[:, 1] atol = 1.0f-6
end

@testset "Stable Fluids solver contract" begin
    uniform = resized_scenario(
        load_scenario(joinpath(REPOSITORY_ROOT, "scenarios", "validation", "uniform.json")),
        (32, 16),
    )
    geometry = NacaFoil(uniform.foil)
    solver = StableFluidsSolver(Float64)
    initialize!(solver, uniform, geometry, uniform.seed)
    @test solver_info(solver).id == "stable-fluids"
    @test reynolds(solver) == uniform.reynolds
    initial = diagnostics(solver)
    report = advance!(solver, control_at(uniform, uniform.output_dt), uniform.output_dt)
    final = diagnostics(solver)
    @test report.requested_dt == uniform.output_dt
    @test report.advanced_dt == uniform.output_dt
    @test report.substeps >= 1
    @test final.values["time"] ≈ uniform.output_dt
    @test final.values["kinetic_energy"] ≈ initial.values["kinetic_energy"] atol = 1.0e-10
    @test final.values["divergence_l2"] < 1.0e-10

    set_reynolds!(solver, 750.0)
    @test reynolds(solver) == 750.0
    @test_throws ArgumentError set_reynolds!(solver, 0.0)
    state = export_state(solver)
    @test state.source_language == "julia"
    @test state.source_solver == "stable-fluids"
    @test state.time ≈ uniform.output_dt
    @test all(isfinite, state.velocity)

    imported = StableFluidsSolver(Float64)
    initialize!(imported, uniform, geometry, uniform.seed)
    import_outcome = import_state!(imported, state, control_at(uniform, state.time))
    @test accepted(import_outcome)
    import_report = something(import_outcome.report)
    @test import_report.source_solver == "stable-fluids"
    @test import_report.destination_solver == "stable-fluids"
    @test cell_velocity(imported) ≈ cell_velocity(solver) atol = 1.0e-10

    chaotic = load_scenario(joinpath(
        REPOSITORY_ROOT, "scenarios", "airfoil", "chaotic-experimental.json",
    ))
    chaotic_geometry = NacaFoil(chaotic.foil)
    chaotic_source = StableFluidsSolver(Float32)
    initialize!(chaotic_source, chaotic, chaotic_geometry, chaotic.seed)
    chaotic_state = export_state(chaotic_source)
    chaotic_destination = StableFluidsSolver(Float32)
    initialize!(chaotic_destination, chaotic, chaotic_geometry, chaotic.seed)
    chaotic_outcome = import_state!(
        chaotic_destination,
        chaotic_state,
        ControlState(
            chaotic_state.time,
            chaotic_state.angle_degrees,
            chaotic_state.angular_velocity_degrees,
        ),
    )
    @test accepted(chaotic_outcome)
end

@testset "D2Q9 TRT kernels and scaling" begin
    for T in (Float32, Float64), shape in ((5, 4), (11, 7))
        density = fill(T(1.07), shape)
        velocity = zeros(T, shape..., 2)
        for j in axes(density, 2), i in axes(density, 1)
            velocity[i, j, 1] = T(0.03) * sin(T(i))
            velocity[i, j, 2] = T(0.02) * cos(T(j))
        end
        populations = lbm_equilibrium(density, velocity)
        recovered_density, recovered_velocity = lbm_macroscopic(populations)
        tolerance = T === Float32 ? 2.0f-6 : 2.0e-14
        @test size(populations) == (9, shape...)
        @test recovered_density ≈ density atol = tolerance
        @test recovered_velocity ≈ velocity atol = tolerance

        collision_density, post = lbm_trt_collision(populations, T(1.3), T(0.8))
        @test collision_density ≈ density atol = tolerance
        @test post ≈ populations atol = tolerance

        disturbed = copy(populations)
        disturbed[2, 3, 2] += T(0.005)
        disturbed[4, 3, 2] -= T(0.005)
        before_density, before_velocity = lbm_macroscopic(disturbed)
        _, relaxed = lbm_trt_collision(disturbed, T(1.1), T(0.9))
        after_density, after_velocity = lbm_macroscopic(relaxed)
        @test after_density ≈ before_density atol = tolerance
        @test after_velocity ≈ before_velocity atol = 4 * tolerance
        @test all(isfinite, relaxed)
    end

    scenario = resized_scenario(
        load_scenario(joinpath(REPOSITORY_ROOT, "scenarios", "airfoil", "default.json")),
        (40, 24),
    )
    scaling = lbm_scaling(scenario)
    @test scaling.lattice_speed <= 0.08f0
    @test 0.0f0 < scaling.omega_plus < 2.0f0
    @test 0.0f0 < scaling.omega_minus < 2.0f0
    @test scaling.effective_reynolds <= scenario.reynolds
    @test_throws ArgumentError lbm_scaling(scenario, 0)
end

@testset "Quadratic PIC/FLIP transfers" begin
    for T in (Float32, Float64), shape in ((5, 4), (8, 6))
        domain = DomainSpec(
            ((T(-1.0), T(1.0)), (T(-0.75), T(0.75))),
            shape,
            (),
        )
        grid = Array{T,3}(undef, shape..., 2)
        for component in 1:2, j in axes(grid, 2), i in axes(grid, 1)
            grid[i, j, component] = T(i + 3 * j + 7 * component) / T(17)
        end
        positions = T[
            -0.99 -0.4 0.97
            -0.74 0.11 0.73
        ]
        gathered = grid_to_particle(grid, positions, domain)
        @test size(gathered) == size(positions)
        @test all(isfinite, gathered)

        constant_grid = zeros(T, shape..., 2)
        constant_grid[:, :, 1] .= T(1.25)
        constant_grid[:, :, 2] .= T(-0.375)
        constant = grid_to_particle(constant_grid, positions, domain)
        tolerance = T === Float32 ? 1.0f-6 : 1.0e-13
        @test constant ≈ repeat(T[1.25, -0.375], 1, 3) atol = tolerance

        particle_velocity = repeat(T[0.75, -0.25], 1, 3)
        scattered = particle_to_grid(positions, particle_velocity, domain, SVector{2,T}(0, 0))
        @test all(isfinite, scattered)
        occupied = particle_cell_counts(positions, domain)
        @test sum(occupied) == 3
        @test length(particle_cell_ids(positions, domain)) == 3
    end

    periodic = DomainSpec(((-1.0f0, 1.0f0), (-0.75f0, 0.75f0)), (8, 6), (:x, :y))
    periodic_grid = reshape(Float32.(1:(8 * 6 * 2)), 8, 6, 2)
    positions = Float32[
        -1.07 0.93 -0.57 -0.57
        -0.44 -0.44 -0.84 0.66
    ]
    gathered = grid_to_particle(periodic_grid, positions, periodic)
    @test gathered[:, 1] ≈ gathered[:, 2] atol = 1.0f-5
    @test gathered[:, 3] ≈ gathered[:, 4] atol = 1.0f-5
    mac_positions = Matrix{Float32}(undef, 2, 48)
    particle = 1
    for y in 0:5, x in 0:7
        mac_positions[1, particle] = -1 + (x + 0.25f0) * 0.25f0
        mac_positions[2, particle] = -0.75f0 + (y + 0.75f0) * 0.25f0
        particle += 1
    end
    mac_velocity = repeat(Float32[0.75, -0.25], 1, size(mac_positions, 2))
    fallback_u = fill(0.75f0, 9, 6)
    fallback_v = fill(-0.25f0, 8, 7)
    mac_u, mac_v, unsupported = particle_to_faces(
        mac_positions, mac_velocity, periodic, fallback_u, fallback_v,
    )
    mac_gathered = faces_to_particle(mac_u, mac_v, mac_positions, periodic)
    mac_gathered_in_place = fill(Float32(NaN), size(mac_velocity))
    @test faces_to_particle!(
        mac_gathered_in_place, mac_u, mac_v, mac_positions, periodic,
    ) === mac_gathered_in_place
    @test mac_u ≈ fallback_u atol = 1.0f-6
    @test mac_v ≈ fallback_v atol = 1.0f-6
    @test mac_gathered ≈ mac_velocity atol = 1.0f-6
    @test mac_gathered_in_place ≈ mac_gathered atol = 1.0f-6
    @test mac_u[1, :] == mac_u[end, :]
    @test mac_v[:, 1] == mac_v[:, end]
    @test 0 <= unsupported < 1
    @test_throws DimensionMismatch grid_to_particle(periodic_grid, zeros(Float32, 3, 2), periodic)
end

@testset "D2Q9 LBM solver contract" begin
    uniform = resized_scenario(
        load_scenario(joinpath(REPOSITORY_ROOT, "scenarios", "validation", "uniform.json")),
        (32, 16),
    )
    geometry = NacaFoil(uniform.foil)
    solver = LBMSolver(Float64)
    initialize!(solver, uniform, geometry, uniform.seed)
    @test solver_info(solver).id == "lbm-d2q9"
    @test reynolds(solver) == uniform.reynolds
    initial = diagnostics(solver)
    for step in 1:4
        report = advance!(solver, control_at(uniform, step * uniform.output_dt), uniform.output_dt)
        @test report.advanced_dt == uniform.output_dt
        @test report.substeps >= 1
    end
    final = diagnostics(solver)
    @test final.values["time"] ≈ 4 * uniform.output_dt
    @test final.values["kinetic_energy"] ≈ initial.values["kinetic_energy"] atol = 1.0e-10
    @test abs(final.values["density_drift"]) < 1.0e-12
    @test final.values["divergence_l2"] < 1.0e-10

    set_reynolds!(solver, 750.0)
    @test reynolds(solver) == 750.0
    @test diagnostics(solver).values["effective_reynolds"] <= 750.0
    @test_throws ArgumentError set_reynolds!(solver, -1.0)
    state = export_state(solver)
    @test state.source_solver == "lbm-d2q9"
    @test state.density !== nothing
    @test all(isfinite, state.velocity)

    imported = LBMSolver(Float64)
    initialize!(imported, uniform, geometry, uniform.seed)
    import_outcome = import_state!(imported, state, control_at(uniform, state.time))
    @test accepted(import_outcome)
    import_report = something(import_outcome.report)
    @test "non-equilibrium lattice populations" in import_report.discarded_state
    @test cell_velocity(imported) ≈ cell_velocity(solver) atol = 1.0e-10

    points = Matrix{Float64}(undef, 2, 2)
    centers = cell_centers(uniform.domain)
    points[:, 1] = centers[4, 4, :]
    points[:, 2] = centers[12, 9, :]
    @test sample_velocity(imported, points) ≈ repeat([1.0, 0.0], 1, 2) atol = 1.0e-10

    open_scenario = resized_scenario(
        load_scenario(joinpath(REPOSITORY_ROOT, "scenarios", "airfoil", "default.json")),
        (40, 24),
    )
    moving = LBMSolver(Float32)
    initialize!(moving, open_scenario, NacaFoil(open_scenario.foil), open_scenario.seed)
    moving_control = ControlState(open_scenario.output_dt, 12.0f0, 120.0f0)
    moving_report = advance!(moving, moving_control, open_scenario.output_dt)
    @test moving_report.advanced_dt == open_scenario.output_dt
    @test all(isfinite, export_state(moving).velocity)
    @test all(isfinite, values(diagnostics(moving).values))
end

@testset "Blended PIC/FLIP solver contract" begin
    uniform = resized_scenario(
        load_scenario(joinpath(REPOSITORY_ROOT, "scenarios", "validation", "uniform.json")),
        (20, 10),
    )
    geometry = NacaFoil(uniform.foil)
    first_solver = PicFlipSolver(Float64)
    second_solver = PicFlipSolver(Float64)
    initialize!(first_solver, uniform, geometry, 17)
    initialize!(second_solver, uniform, geometry, 17)
    @test solver_info(first_solver).id == "pic-flip"
    @test first_solver.positions == second_solver.positions
    @test first_solver.particle_velocity == second_solver.particle_velocity
    @test size(first_solver.positions) == (2, 4 * 20 * 10)
    speed_probe = PicFlipSolver(Float64)
    initialize!(speed_probe, uniform, geometry, 17)
    speed_probe.grid_velocity .= 0
    speed_probe.particle_velocity .= 0
    speed_probe.particle_velocity[1, end] = 100
    @test FoilBenchJulia._pic_maximum_speed(speed_probe, uniform) == 100
    @test pic_flip_blend(first_solver) ≈ 0.95
    @test set_pic_flip_blend!(first_solver, 2.0) == 1.0
    @test set_pic_flip_blend!(first_solver, 0.95) ≈ 0.95

    initial = diagnostics(first_solver)
    for step in 1:8
        report = advance!(
            first_solver,
            control_at(uniform, step * uniform.output_dt),
            uniform.output_dt,
        )
        @test report.advanced_dt == uniform.output_dt
        @test report.substeps >= 1
    end
    final = diagnostics(first_solver)
    @test final.values["time"] ≈ 8 * uniform.output_dt
    @test final.values["particle_count"] == initial.values["particle_count"]
    @test final.values["particles_inside_solid"] == 0
    @test final.values["kinetic_energy"] ≈ initial.values["kinetic_energy"] atol = 1.0e-8
    @test all(isfinite, values(final.values))

    set_reynolds!(first_solver, 750.0)
    @test reynolds(first_solver) == 750.0
    state = export_state(first_solver)
    imported = PicFlipSolver(Float64)
    initialize!(imported, uniform, geometry, uniform.seed)
    import_outcome = import_state!(imported, state, control_at(uniform, state.time))
    @test accepted(import_outcome)
    import_report = something(import_outcome.report)
    @test "solver particles" in import_report.discarded_state
    @test imported.settling_steps == 1
    @test cell_velocity(imported) ≈ cell_velocity(first_solver) atol = 1.0e-10

    open_scenario = resized_scenario(
        load_scenario(joinpath(REPOSITORY_ROOT, "scenarios", "airfoil", "default.json")),
        (32, 20),
    )
    moving = PicFlipSolver(Float32)
    initialize!(moving, open_scenario, NacaFoil(open_scenario.foil), open_scenario.seed)
    moving_control = ControlState(open_scenario.output_dt, 18.0f0, 180.0f0)
    moving_report = advance!(moving, moving_control, open_scenario.output_dt)
    @test moving_report.substeps >= 2
    @test diagnostics(moving).values["particles_inside_solid"] == 0
    @test all(isfinite, export_state(moving).velocity)
end

@testset "Revision 2 solver transactions" begin
    scenario = resized_scenario(
        load_scenario(joinpath(REPOSITORY_ROOT, "scenarios", "airfoil", "default.json")),
        (32, 16),
    )
    geometry = NacaFoil(scenario.foil)
    for solver in (StableFluidsSolver(Float32), LBMSolver(Float32), PicFlipSolver(Float32))
        initialize!(solver, scenario, geometry, scenario.seed)
        @test state_revision(solver) == 0
        report = advance!(solver, control_at(scenario, scenario.output_dt), scenario.output_dt)
        @test report.advanced_dt == scenario.output_dt
        @test report.state_revision == 1
        @test diagnostics(solver).state_revision == report.state_revision
        exported = export_state(solver)
        @test exported.time == scenario.output_dt
        exported_solid = solid_mask(geometry, scenario.domain, exported.angle_degrees)
        exported_velocity = canonical_to_cell(exported)
        @test all(
            exported_velocity[index, component] == 0
            for index in CartesianIndices(exported_solid) if exported_solid[index]
            for component in 1:2
        )
    end

    for solver in (StableFluidsSolver(Float32), LBMSolver(Float32), PicFlipSolver(Float32))
        initialize!(solver, scenario, geometry, scenario.seed)
        previous_reynolds = reynolds(solver)
        previous_revision = state_revision(solver)
        previous_state = export_state(solver)
        @test_throws ArgumentError set_reynolds!(solver, 1.0e-100)
        @test reynolds(solver) == previous_reynolds
        @test state_revision(solver) == previous_revision
        @test export_state(solver).velocity == previous_state.velocity
    end

    for solver_type in (StableFluidsSolver, LBMSolver, PicFlipSolver)
        source = solver_type(Float32)
        initialize!(source, scenario, geometry, scenario.seed)
        corrupted = export_state(source)
        corrupted.velocity[1, 1, 1, 1] = NaN32
        destination = solver_type(Float32)
        initialize!(destination, scenario, geometry, scenario.seed)
        outcome = import_state!(
            destination,
            corrupted,
            ControlState(
                corrupted.time,
                corrupted.angle_degrees,
                corrupted.angular_velocity_degrees,
            ),
        )
        @test !accepted(outcome)
        @test outcome.reason == :nonfinite_state
        @test state_revision(destination) == 0
        @test all(isfinite, export_state(destination).velocity)
    end

    solid_scenario = resized_scenario(scenario, (64, 32))
    solid_geometry = NacaFoil(solid_scenario.foil)
    solid_source = StableFluidsSolver(Float32)
    initialize!(solid_source, solid_scenario, solid_geometry, solid_scenario.seed)
    solid_state = export_state(solid_source)
    import_solid = solid_mask(
        solid_geometry, solid_scenario.domain, solid_state.angle_degrees,
    )
    selected = findfirst(import_solid)
    @test selected !== nothing
    if selected !== nothing
        solid_state.velocity[1, selected[2], selected[1], 1] = 0.125f0
        for solver_type in (StableFluidsSolver, LBMSolver, PicFlipSolver)
            destination = solver_type(Float32)
            initialize!(
                destination, solid_scenario, solid_geometry, solid_scenario.seed,
            )
            before = export_state(destination)
            outcome = import_state!(
                destination,
                solid_state,
                ControlState(
                    solid_state.time,
                    solid_state.angle_degrees,
                    solid_state.angular_velocity_degrees,
                ),
            )
            @test !accepted(outcome)
            @test outcome.reason == :postcondition_failure
            @test outcome.stage == Symbol("canonical-import")
            @test outcome.evidence["nonzero_solid_cells"] > 0
            @test state_revision(destination) == 0
            @test export_state(destination).velocity == before.velocity
        end
    end

    density_source = LBMSolver(Float32)
    initialize!(density_source, scenario, geometry, scenario.seed)
    corrupted_density = export_state(density_source)
    @test corrupted_density.density !== nothing
    if corrupted_density.density !== nothing
        corrupted_density.density[1, 1, 1] = NaN32
        density_destination = LBMSolver(Float32)
        initialize!(density_destination, scenario, geometry, scenario.seed)
        outcome = import_state!(
            density_destination,
            corrupted_density,
            ControlState(
                corrupted_density.time,
                corrupted_density.angle_degrees,
                corrupted_density.angular_velocity_degrees,
            ),
        )
        @test !accepted(outcome)
        @test outcome.reason == :nonfinite_state
        @test state_revision(density_destination) == 0
    end


    short_cadence = scenario_with_output_dt(scenario, 0.01)
    long_cadence = scenario_with_output_dt(scenario, 1.0)
    cadence_source = LBMSolver(Float32)
    initialize!(cadence_source, short_cadence, geometry, scenario.seed)
    cadence_state = export_state(cadence_source)
    cadence_state.velocity[:, :, :, 1] .= 10.0f0
    cadence_state.velocity[:, :, :, 2] .= 0.0f0
    cadence_control = ControlState(
        cadence_state.time,
        cadence_state.angle_degrees,
        0.0f0,
    )
    cadence_destinations = LBMSolver{Float32}[]
    for cadence_scenario in (short_cadence, long_cadence)
        destination = LBMSolver(Float32)
        initialize!(destination, cadence_scenario, geometry, scenario.seed)
        @test accepted(import_state!(destination, cadence_state, cadence_control))
        push!(cadence_destinations, destination)
    end
    @test export_state(cadence_destinations[1]).velocity ==
        export_state(cadence_destinations[2]).velocity
    @test diagnostics(cadence_destinations[1]).values["effective_reynolds"] ≈
        diagnostics(cadence_destinations[2]).values["effective_reynolds"]

    shorter = LBMSolver(Float32)
    longer = LBMSolver(Float32)
    initialize!(shorter, scenario, geometry, scenario.seed)
    initialize!(longer, scenario, geometry, scenario.seed)
    short_report = advance!(shorter, ControlState(0.0075f0, 4.0f0, 0.0f0), 0.0075f0)
    long_report = advance!(longer, ControlState(0.01f0, 4.0f0, 0.0f0), 0.01f0)
    @test short_report.advanced_dt == 0.0075f0
    @test long_report.advanced_dt == 0.01f0
    @test export_state(shorter).velocity != export_state(longer).velocity

    stable = StableFluidsSolver(Float32)
    initialize!(stable, scenario, geometry, scenario.seed)
    stable_before = export_state(stable)
    excessive_velocity = fill(1.0f6, size(stable_before.velocity))
    excessive_solid = solid_mask(
        geometry,
        scenario.domain,
        stable_before.angle_degrees,
    )
    for index in CartesianIndices(excessive_solid)
        excessive_solid[index] || continue
        excessive_velocity[1, index[1], index[2], :] .= 0
    end
    stable_import = CanonicalFlowState(
        1, stable_before.bounds, stable_before.resolution, stable_before.periodic_axes,
        0.25f0, stable_before.angle_degrees, 0.0f0, "julia", "injected", excessive_velocity,
    )
    stable_outcome = import_state!(stable, stable_import, ControlState(
        0.25f0,
        stable_before.angle_degrees,
        0.0f0,
    ))
    @test !accepted(stable_outcome)
    @test stable_outcome.reason == :excessive_velocity
    @test state_revision(stable) == 0
    @test export_state(stable).time == stable_before.time
    @test export_state(stable).velocity == stable_before.velocity

    lattice = LBMSolver(Float32)
    initialize!(lattice, scenario, geometry, scenario.seed)
    lattice_before = export_state(lattice)
    invalid_density = fill(2.0f0, size(something(lattice_before.density)))
    lattice_import = CanonicalFlowState(
        1, lattice_before.bounds, lattice_before.resolution, lattice_before.periodic_axes,
        0.25f0, lattice_before.angle_degrees, 0.0f0, "julia", "injected", copy(lattice_before.velocity),
        invalid_density,
    )
    lattice_outcome = import_state!(lattice, lattice_import, ControlState(
        0.25f0,
        lattice_before.angle_degrees,
        0.0f0,
    ))
    @test !accepted(lattice_outcome)
    @test lattice_outcome.reason == :invalid_density
    @test state_revision(lattice) == 0
    @test export_state(lattice).time == lattice_before.time
    @test export_state(lattice).velocity == lattice_before.velocity

    solid_density_source = LBMSolver(Float32)
    initialize!(solid_density_source, scenario, geometry, scenario.seed)
    solid_density_state = export_state(solid_density_source)
    @test solid_density_state.density !== nothing
    if solid_density_state.density !== nothing
        modified_density = copy(solid_density_state.density)
        import_solid = solid_mask(
            geometry,
            scenario.domain,
            solid_density_state.angle_degrees,
        )
        for j in 1:ny(scenario.domain), i in 1:nx(scenario.domain)
            import_solid[i, j] && (modified_density[1, j, i] = 100.0f0)
        end
        modified_state = CanonicalFlowState(
            solid_density_state.schema_version,
            solid_density_state.bounds,
            solid_density_state.resolution,
            solid_density_state.periodic_axes,
            solid_density_state.time,
            solid_density_state.angle_degrees,
            solid_density_state.angular_velocity_degrees,
            solid_density_state.source_language,
            solid_density_state.source_solver,
            copy(solid_density_state.velocity),
            modified_density,
            solid_density_state.geometry,
            solid_density_state.producer_execution_target,
        )
        solid_density_destination = LBMSolver(Float32)
        initialize!(solid_density_destination, scenario, geometry, scenario.seed)
        outcome = import_state!(
            solid_density_destination,
            modified_state,
            ControlState(
                solid_density_state.time,
                solid_density_state.angle_degrees,
                solid_density_state.angular_velocity_degrees,
            ),
        )
        @test accepted(outcome)
    end
end

@testset "Shared Revision 4 solver-validity fixture" begin
    fixture = JSON3.read(read(joinpath(FIXTURES, "solver-validity.json"), String))
    @test fixture.contract_id == "foilbench-phase2-v1"
    @test fixture.contract_revision == 4
    fixture_scenario = resized_scenario(
        load_scenario(joinpath(REPOSITORY_ROOT, fixture.scenario)),
        (Int(fixture.resolution[1]), Int(fixture.resolution[2])),
    )
    geometry = NacaFoil(fixture_scenario.foil)
    T = scalar_type(fixture_scenario)
    for solver in (
        StableFluidsSolver(T),
        LBMSolver(T),
        PicFlipSolver(T),
    )
        solver_id = solver_info(solver).id
        initialize!(solver, fixture_scenario, geometry, fixture_scenario.seed)
        @test state_revision(solver) == 0
        set_reynolds!(solver, fixture.changed_reynolds)
        @test state_revision(solver) == 1
        set_reynolds!(solver, fixture.changed_reynolds)
        @test state_revision(solver) == 1
        report = advance!(
            solver,
            control_at(fixture_scenario, fixture.target_dt),
            fixture.target_dt,
        )
        for key in fixture.accepted_evidence[solver_id]
            @test haskey(report.evidence, String(key))
            value = report.evidence[String(key)]
            if endswith(String(key), "_converged")
                @test value === true
            else
                @test value isa Real && isfinite(value)
            end
        end
        @test report.state_revision == state_revision(solver) == 2
        @test diagnostics(solver).state_revision == report.state_revision
        if solver_id == "stable-fluids"
            @test report.evidence["maximum_characteristic_displacement"] <=
                fixture.limits.stable_maximum_characteristic_displacement
            @test report.evidence["maximum_boundary_sweep"] <=
                fixture.limits.stable_maximum_boundary_sweep
        elseif solver_id == "lbm-d2q9"
            @test report.evidence["maximum_lattice_mach"] <=
                fixture.limits.lbm_maximum_mach * (1 + 1.0e-6)
            @test report.evidence["density_excursion"] <=
                fixture.limits.lbm_maximum_density_excursion
            @test report.evidence["minimum_population"] >=
                fixture.limits.lbm_minimum_population
            @test report.evidence["trt_magic"] ≈ fixture.limits.lbm_trt_magic
        elseif solver_id == "pic-flip"
            @test report.evidence["maximum_particle_cfl"] <=
                fixture.limits.pic_maximum_particle_cfl * (1 + 1.0e-6)
            @test report.evidence["empty_cell_fraction"] <=
                fixture.limits.pic_maximum_empty_cell_fraction
            @test report.evidence["underfilled_cell_fraction"] <=
                fixture.limits.pic_maximum_underfilled_cell_fraction
            @test report.evidence["unsupported_face_fraction"] <=
                fixture.limits.pic_maximum_unsupported_face_fraction
            @test report.evidence["unresolved_solid_particles"] <=
                fixture.limits.pic_maximum_unresolved_solid_particles
        end
        if solver_id in ("stable-fluids", "pic-flip")
            @test report.evidence["pressure_relative_residual"] <=
                fixture.limits.pressure_maximum_relative_residual
            @test report.evidence["viscosity_final_residual"] <=
                fixture.limits.viscosity_maximum_final_residual
            @test report.evidence["divergence_linf"] <=
                fixture.limits.mac_maximum_divergence_linf
            @test report.evidence["solid_leakage"] <=
                fixture.limits.mac_maximum_solid_leakage
        end
        diagnostic_values = diagnostics(solver).values
        @test isfinite(diagnostic_values["solid_leakage"])
        if solver_id in ("stable-fluids", "pic-flip")
            @test isfinite(diagnostic_values["divergence_linf"])
        else
            @test !haskey(diagnostic_values, "divergence_linf")
            @test diagnostic_values["solid_leakage"] == 0.0
            @test isfinite(diagnostic_values["cut_link_adjacent_normal_speed"])
        end

        mismatch = solver_id == "stable-fluids" ? StableFluidsSolver(T) :
            solver_id == "lbm-d2q9" ? LBMSolver(T) : PicFlipSolver(T)
        initialize!(mismatch, fixture_scenario, geometry, fixture_scenario.seed)
        before = export_state(mismatch)
        mismatch_failure = try
            advance!(
                mismatch,
                ControlState(T(fixture.invalid_completion_time), zero(T), zero(T)),
                fixture.target_dt,
            )
            nothing
        catch failure
            failure
        end
        @test mismatch_failure isa NumericalFailure
        if mismatch_failure isa NumericalFailure
            @test mismatch_failure.reason == Symbol(fixture.invalid_completion_reason)
            @test mismatch_failure.stage == Symbol(fixture.invalid_completion_stage)
        end
        @test export_state(mismatch).time == before.time
        @test export_state(mismatch).velocity == before.velocity

        extreme = solver_id == "stable-fluids" ? StableFluidsSolver(T) :
            solver_id == "lbm-d2q9" ? LBMSolver(T) : PicFlipSolver(T)
        initialize!(extreme, fixture_scenario, geometry, fixture_scenario.seed)
        before = export_state(extreme)
        extreme_failure = try
            advance!(
                extreme,
                ControlState(
                    T(fixture.target_dt),
                    zero(T),
                    T(fixture.extreme_angular_velocity_degrees),
                ),
                fixture.target_dt,
            )
            nothing
        catch failure
            failure
        end
        @test extreme_failure isa NumericalFailure
        if extreme_failure isa NumericalFailure
            @test String(extreme_failure.reason) in fixture.extreme_motion_allowed_reasons
            @test extreme_failure.evidence["required_substeps"] >
                fixture.maximum_internal_substeps
        end
        @test export_state(extreme).time == before.time
        @test export_state(extreme).velocity == before.velocity
    end
end

function retry_scenario(case)
    scenario = load_scenario(joinpath(REPOSITORY_ROOT, String(case.scenario)))
    resolution = Tuple(Int.(case.resolution))
    domain = DomainSpec(scenario.domain.bounds, resolution, scenario.domain.periodic_axes)
    options = copy(scenario.solver_options)
    for (key, value) in pairs(case.solver_options)
        options[String(key)] = value
    end
    T = typeof(scenario.output_dt)
    return Scenario(
        scenario.schema_version,
        scenario.id,
        domain,
        scenario.reynolds,
        scenario.freestream,
        scenario.foil,
        scenario.controls,
        scenario.duration,
        T(case.target_dt),
        scenario.precision,
        scenario.seed,
        options,
    )
end

@testset "MAC postcondition rejection is transactional" begin
    scenario = resized_scenario(
        load_scenario(joinpath(REPOSITORY_ROOT, "scenarios", "airfoil", "fixed-stall.json")),
        (32, 16),
    )
    scenario.solver_options["mac_maximum_divergence_linf"] = 0.0
    scenario.solver_options["mac_maximum_solid_leakage"] = 0.0
    geometry = NacaFoil(scenario.foil)
    T = scalar_type(scenario)
    for solver in (StableFluidsSolver(T), PicFlipSolver(T))
        initialize!(solver, scenario, geometry, scenario.seed)
        before = export_state(solver)
        failure = try
            advance!(
                solver,
                ControlState(T(scenario.output_dt), T(25), zero(T)),
                scenario.output_dt,
            )
            nothing
        catch error
            error
        end
        @test failure isa NumericalFailure
        @test failure.reason == :postcondition_failure
        @test failure.stage == :postcondition
        @test failure.evidence["divergence_linf"] > 0
        @test state_revision(solver) == 0
        after = export_state(solver)
        @test after.time == before.time
        @test after.velocity == before.velocity
    end
end

@testset "Shared Revision 4 tracer fixture" begin
    fixture = JSON3.read(read(joinpath(FIXTURES, "tracer-lifecycle.json"), String))
    @test fixture.contract_id == "foilbench-phase2-v1"
    @test fixture.contract_revision == 4
    scenario = resized_scenario(
        load_scenario(joinpath(REPOSITORY_ROOT, "scenarios", "airfoil", "default.json")),
        (32, 16),
    )
    geometry = NacaFoil(scenario.foil)
    T = scalar_type(scenario)
    tracers = TracerState(scenario, geometry, zero(T); count = 1, history_length = 3)
    tracers.positions[:, 1] .= T.(fixture.integrator.initial_position)
    tracers.ages[1] = zero(T)
    tracers.lifetimes[1] = T(10)
    advance_tracers!(
        tracers,
        RotationTracerSolver{T}(),
        scenario,
        geometry,
        ControlState(T(fixture.integrator.target_dt), zero(T), zero(T)),
        T(fixture.integrator.target_dt),
    )
    @test tracers.positions[:, 1] ≈ T.(fixture.integrator.expected_position) atol =
        T(fixture.integrator.absolute_tolerance)
end

@testset "Shared Revision 4 vorticity-display fixture" begin
    fixture = JSON3.read(read(joinpath(FIXTURES, "vorticity-display.json"), String))
    @test fixture.contract_id == "foilbench-phase2-v1"
    @test fixture.contract_revision == 4
    recipe = fixture.synthetic
    raw = reshape(
        vcat(
            Float32.(0:(recipe.linear_count - 1)) .* Float32(recipe.linear_step),
            Float32(recipe.outlier),
        ),
        :,
        1,
    )

    solid = falses(size(raw))
    solid[end] = true
    masked = FoilBenchJulia._normalize_viewer_vorticity!(copy(raw), solid)
    @test masked[end] == 0f0
    @test masked[end - 1] ≈
        tanh(1.99f0 / Float32(recipe.solid_outlier_expected_scale)) atol = 1f-6

    unmasked = FoilBenchJulia._normalize_viewer_vorticity!(copy(raw), falses(size(raw)))
    @test unmasked[end] ≈
        tanh(Float32(recipe.outlier / recipe.fluid_outlier_expected_scale)) atol = 1f-6
    @test unmasked[end - 1] ≈
        tanh(1.99f0 / Float32(recipe.fluid_outlier_expected_scale)) atol = 1f-6
end

@testset "Stable Fluids validation modes" begin
    taylor_green = resized_scenario(
        load_scenario(joinpath(REPOSITORY_ROOT, "scenarios", "validation", "taylor-green.json")),
        (32, 32),
    )
    taylor_solver = StableFluidsSolver(Float64)
    initialize!(taylor_solver, taylor_green, NacaFoil(taylor_green.foil), 0)
    initial_energy = diagnostics(taylor_solver).values["kinetic_energy"]
    advance!(
        taylor_solver,
        control_at(taylor_green, taylor_green.output_dt),
        taylor_green.output_dt,
    )
    taylor_diagnostics = diagnostics(taylor_solver)
    @test 0.0 < taylor_diagnostics.values["kinetic_energy"] <= initial_energy
    @test taylor_diagnostics.values["divergence_l2"] < 0.12

    for solver in (StableFluidsSolver(Float64), PicFlipSolver(Float64))
        restart!(
            solver,
            taylor_green,
            NacaFoil(taylor_green.foil),
            0,
            RestartState(0.5, 0.0, taylor_green.reynolds),
        )
        @test maximum(abs, view(cell_velocity(solver), :, :, 2)) > 0.25
    end

    retry_fixture = JSON3.read(read(joinpath(FIXTURES, "solver-validity.json"), String))
    retry_case = retry_fixture.planning_retry_cases["stable-fluids"]
    chaotic = retry_scenario(retry_case)
    chaotic_solver = StableFluidsSolver(Float32)
    initialize!(chaotic_solver, chaotic, NacaFoil(chaotic.foil), 0)
    total_retries = 0
    chaotic_report = nothing
    control = ControlState(
        chaotic.output_dt,
        typeof(chaotic.output_dt)(retry_case.angle_degrees),
        typeof(chaotic.output_dt)(retry_case.angular_velocity_degrees),
    )
    chaotic_report = advance!(chaotic_solver, control, chaotic.output_dt)
    @test chaotic_report.state_revision == 1
    total_retries += Int(chaotic_report.evidence["stability_retries"])
    @test chaotic_report !== nothing
    @test chaotic_report.advanced_dt == chaotic.output_dt
    @test export_state(chaotic_solver).time == chaotic.output_dt
    @test total_retries >= retry_case.minimum_total_stability_retries
    @test all(isfinite, export_state(chaotic_solver).velocity)

    pic_case = retry_fixture.planning_retry_cases["pic-flip"]
    pic_scenario = retry_scenario(pic_case)
    pic_solver = PicFlipSolver(Float32)
    initialize!(pic_solver, pic_scenario, NacaFoil(pic_scenario.foil), pic_scenario.seed)
    pic_report = advance!(
        pic_solver,
        ControlState(
            pic_scenario.output_dt,
            typeof(pic_scenario.output_dt)(pic_case.angle_degrees),
            typeof(pic_scenario.output_dt)(pic_case.angular_velocity_degrees),
        ),
        pic_scenario.output_dt,
    )
    @test pic_report.substeps >= 1
    @test pic_report.evidence["stability_retries"] >=
        pic_case.minimum_total_stability_retries
    @test pic_report.evidence["maximum_particle_cfl"] <=
        option(pic_scenario, "pic_cfl", 0.75) * (1 + 1.0e-6)
    @test all(isfinite, export_state(pic_solver).velocity)

    lbm_case = retry_fixture.planning_retry_cases["lbm-d2q9"]
    lbm_scenario = load_scenario(joinpath(REPOSITORY_ROOT, String(lbm_case.scenario)))
    lbm_solver = LBMSolver(Float32)
    restart!(
        lbm_solver,
        lbm_scenario,
        NacaFoil(lbm_scenario.foil),
        lbm_scenario.seed,
        RestartState(0.0f0, Float32(lbm_case.angle_degrees), lbm_scenario.reynolds),
    )
    lbm_report = advance!(
        lbm_solver,
        ControlState(lbm_scenario.output_dt, Float32(lbm_case.angle_degrees), 0.0f0),
        lbm_scenario.output_dt,
    )
    @test lbm_report.evidence["stability_retries"] >=
        lbm_case.minimum_total_stability_retries
    @test lbm_report.evidence["maximum_lattice_mach"] <= 0.08 * (1 + 1.0e-6)
end

@testset "Headless viewer model and worker" begin
    scenario = resized_scenario(
        load_scenario(joinpath(REPOSITORY_ROOT, "scenarios", "validation", "uniform.json")),
        (24, 12),
    )
    model = ViewerModel(scenario; tracer_count = 32, history_length = 5)
    initial = snapshot(model)
    @test all(model.tracers.ages .>= 0)
    @test all(model.tracers.ages .< model.tracers.lifetimes)
    @test any(model.tracers.ages .> 0)
    @test initial.time == 0.0
    @test initial.solver_epoch == 0
    @test initial.solver_state_revision == state_revision(model.solver)
    @test initial.diagnostic_solver_state_revision == state_revision(model.solver)
    @test initial.vorticity_solver_state_revision == state_revision(model.solver)
    @test size(initial.tracer_positions) == (2, 32)
    @test size(initial.path_segments) == (2, 2 * 32 * 4)
    @test size(initial.vorticity) == (24, 12)
    @test maximum(abs, initial.vorticity) <= 1
    @test size(foil_outline(model.geometry, initial.angle_degrees; samples = 32)) == (2, 32)
    @test occursin("AoA=", initial.status)
    @test occursin("sim/wall=", initial.status)
    @test occursin("tracers=display", initial.status)
    @test initial.vorticity_visible
    @test !toggle_crop!(model)
    @test !model.presentation.crop_enabled

    angle_model = ViewerModel(scenario; tracer_count = 32, history_length = 5)
    set_angle!(angle_model, 12.0, 1.0)
    angle_snapshot = snapshot(angle_model)
    @test angle_snapshot.angle_degrees == 12.0
    @test occursin("AoA= -12.0°", angle_snapshot.status)

    periodic_tracer = 1
    x0, x1 = scenario.domain.bounds[1]
    y0, y1 = scenario.domain.bounds[2]
    model.tracers.positions[:, periodic_tracer] .=
        (x1 - 0.25 * scenario.output_dt, (y0 + y1) / 2)
    for history_index in axes(model.tracers.history, 3)
        model.tracers.history[:, periodic_tracer, history_index] =
            model.tracers.positions[:, periodic_tracer]
    end
    periodic_age = model.tracers.ages[periodic_tracer]
    periodic_lifetime = model.tracers.lifetimes[periodic_tracer]
    periodic_generation = model.tracers.generations[periodic_tracer]
    advance_tracers!(
        model.tracers,
        model.solver,
        scenario,
        model.geometry,
        control_at(scenario, scenario.output_dt),
        scenario.output_dt,
    )
    @test x0 <= model.tracers.positions[1, periodic_tracer] < x0 + scenario.output_dt
    @test model.tracers.ages[periodic_tracer] ≈ periodic_age + scenario.output_dt
    @test model.tracers.lifetimes[periodic_tracer] == periodic_lifetime
    @test model.tracers.generations[periodic_tracer] == periodic_generation + 1
    @test size(path_segments(model.tracers), 2) == 2 * 32 * 4
    @test model.tracers.recycle_counters[:periodic_wrap] == 1

    hidden_model = ViewerModel(scenario; tracer_count = 8, history_length = 3)
    diagnostic_type = scalar_type(scenario)
    hidden_model.presentation.diagnostic_interval = diagnostic_type(0.001)
    @test !toggle_vorticity!(hidden_model)
    hidden_vorticity = copy(hidden_model.presentation.vorticity)
    update!(hidden_model)
    @test hidden_model.presentation.vorticity == hidden_vorticity
    @test isempty(snapshot(hidden_model).vorticity)
    @test toggle_vorticity!(hidden_model)
    @test hidden_model.presentation.diagnostic_elapsed == zero(diagnostic_type)

    schedule_model = ViewerModel(scenario; tracer_count = 8, history_length = 3)
    adjust_reynolds!(schedule_model, 0.25)
    @test schedule_model.manual_angle === nothing
    @test accepted(switch_solver!(schedule_model, "lbm-d2q9"))
    @test schedule_model.manual_angle === nothing
    set_angle!(schedule_model, 18.0, 1.0)
    recover_solver!(schedule_model, ArgumentError("non-finite injected state"))
    @test schedule_model.manual_angle == 18.0
    reset_viewer!(schedule_model)
    @test schedule_model.manual_angle === nothing

    rejected_model = ViewerModel(scenario; tracer_count = 8, history_length = 3)
    rejected = switch_solver!(rejected_model, "unavailable-solver")
    @test !accepted(rejected)
    @test rejected.reason == :unsupported_conversion
    @test solver_info(rejected_model.solver).id == "stable-fluids"

    post_import_model = ViewerModel(
        scenario;
        solver_id = "lbm-d2q9",
        tracer_count = 8,
        history_length = 3,
    )
    @test accepted(switch_solver!(post_import_model, "stable-fluids"))
    @test post_import_model.warm_validation_pending
    post_import_model.solver = FailingStepSolver(post_import_model.solver)
    post_import_failure = try
        update!(post_import_model)
        nothing
    catch error
        error
    end
    @test post_import_failure isa NumericalFailure
    @test post_import_model.warm_validation_pending
    recover_solver!(
        post_import_model,
        post_import_failure;
        post_import = post_import_model.warm_validation_pending,
    )
    @test occursin("stage=post-import", post_import_model.status_message)
    @test accepted(switch_solver!(post_import_model, "pic-flip"))
    @test post_import_model.warm_validation_pending
    update!(post_import_model)
    @test !post_import_model.warm_validation_pending

    tracer_scenario = resized_scenario(
        load_scenario(joinpath(REPOSITORY_ROOT, "scenarios", "airfoil", "default.json")),
        (24, 12),
    )
    tracer_model = ViewerModel(tracer_scenario; tracer_count = 4, history_length = 3)
    tracer_geometry = tracer_model.geometry
    tracer_solver = tracer_model.solver
    inlet_tracers = tracer_model.tracers
    @test option(tracer_scenario, "viewer_crop_cells", 0) == 4
    @test !tracer_model.presentation.crop_enabled
    @test toggle_crop!(tracer_model)
    @test set_angle!(tracer_model, 90, 1.0) == 30
    @test set_angle!(tracer_model, -90, 2.0) == -30
    prior_pose = tracer_model.manual_angle
    prior_drag = tracer_model.drag_active
    @test_throws ArgumentError set_angle!(tracer_model, NaN, 3.0)
    @test_throws ArgumentError set_angle!(tracer_model, 5.0, Inf)
    @test tracer_model.manual_angle == prior_pose
    @test tracer_model.drag_active == prior_drag
    tracer_model.tracers.positions[:, 1] .= (0, 0)
    shallow_generation = tracer_model.tracers.generations[1]
    advance_tracers!(
        tracer_model.tracers,
        tracer_model.solver,
        tracer_scenario,
        tracer_geometry,
        control_at(tracer_scenario, 0),
        zero(scalar_type(tracer_scenario)),
    )
    @test signed_distance(
        tracer_geometry,
        SVector{2,scalar_type(tracer_scenario)}(tracer_model.tracers.positions[:, 1]),
        control_at(tracer_scenario, 0).angle_degrees,
    ) > 0
    @test tracer_model.tracers.generations[1] == shallow_generation
    x0, x1 = tracer_scenario.domain.bounds[1]
    y0, y1 = tracer_scenario.domain.bounds[2]
    inlet_tracers.positions[:, 1] .=
        (x1 + dx(tracer_scenario.domain), (y0 + y1) / 2)
    inlet_tracers.positions[:, 2] .=
        (x0 + dx(tracer_scenario.domain), y1 - dy(tracer_scenario.domain))
    inlet_tracers.ages[2] = 0
    inlet_tracers.lifetimes[2] = 7
    advance_tracers!(
        inlet_tracers,
        tracer_solver,
        tracer_scenario,
        tracer_geometry,
        control_at(tracer_scenario, tracer_scenario.output_dt),
        tracer_scenario.output_dt,
    )
    @test x0 <= inlet_tracers.positions[1, 1] <=
        x0 + 0.5 * dx(tracer_scenario.domain)
    @test inlet_tracers.ages[1] == 0
    @test inlet_tracers.ages[2] == tracer_scenario.output_dt
    for history_index in axes(inlet_tracers.history, 3)
        @test inlet_tracers.history[:, 1, history_index] == inlet_tracers.positions[:, 1]
    end

    updated = update!(model)
    @test updated.time == scenario.output_dt
    @test all(isfinite, updated.tracer_positions)
    @test all(isfinite, updated.path_segments)
    @test model.last_substeps >= 1
    @test isfinite(model.last_max_speed)
    @test model.simulated_seconds_per_wall_second > 0
    @test occursin("max|u|=", updated.status)
    @test occursin("E=", updated.status)
    @test occursin("Ω=", updated.status)
    @test stable_transport_mode(model.solver::StableFluidsSolver) == "maccormack"
    stable_time = diagnostics(model.solver).values["time"]
    stable_velocity = copy(export_state(model.solver).velocity)
    @test adjust_tuning!(model, 0.05)
    @test stable_transport_mode(model.solver::StableFluidsSolver) == "skew-rk2"
    @test diagnostics(model.solver).values["time"] == stable_time
    @test export_state(model.solver).velocity == stable_velocity
    @test occursin("adv=skew-rk2", snapshot(model).status)

    set_angle!(model, 12.0, 1.0)
    @test model.angular_velocity == 0.0
    set_angle!(model, 13.0, 1.05)
    @test model.manual_angle == 13.0
    @test 0 < requested_tip_speed_ratio(model) <= 8
    set_angle!(model, 10.0, 1.04)
    @test model.angular_velocity == 0.0
    release_angle!(model)
    @test model.angular_velocity == 0.0
    adjust_reynolds!(model, 1.0)
    @test reynolds(model.solver) ≈ 10 * scenario.reynolds
    @test model.playback_rate ≈ 1.5
    reset_reynolds!(model)
    @test reynolds(model.solver) == scenario.reynolds
    @test toggle_tracer_mode!(model) == :material
    @test !toggle_vorticity!(model)
    @test toggle_diagnostics!(model) == Symbol("every-step")
    toggled = snapshot(model)
    @test occursin("tracers=material", toggled.status)
    @test occursin("vort=off", toggled.status)
    @test toggled.diagnostic_mode == Symbol("every-step")
    @test toggle_pause!(model)
    @test update!(model).time == updated.time
    @test accepted(switch_solver!(model, "lbm-d2q9"))
    @test solver_info(model.solver).id == "lbm-d2q9"
    @test model.simulation_time ≈ updated.time + scenario.output_dt
    @test diagnostics(model.solver).values["time"] ≈ model.simulation_time
    @test !adjust_tuning!(model, 0.05)
    @test occursin("no adjustable tuning", model.status_message)
    @test accepted(switch_solver!(model, "stable-fluids"))
    @test solver_info(model.solver).id == "stable-fluids"
    @test stable_transport_mode(model.solver::StableFluidsSolver) == "skew-rk2"
    @test accepted(switch_solver!(model, "pic-flip"))
    @test solver_info(model.solver).id == "pic-flip"
    @test adjust_tuning!(model, -0.05)
    @test pic_flip_blend(model.solver) ≈ 0.9
    @test accepted(switch_solver!(model, "stable-fluids"))
    @test stable_transport_mode(model.solver::StableFluidsSolver) == "skew-rk2"
    reset_viewer!(model)
    @test diagnostics(model.solver).values["time"] == 0.0
    @test model.status_message == "reset"
    @test snapshot(model).diagnostic_mode == Symbol("every-step")
    @test viewer_session_state(model).diagnostic_mode == Symbol("every-step")
    @test stable_transport_mode(model.solver::StableFluidsSolver) == "maccormack"

    for failure in (:tracer, :diagnostic)
        presentation_model = ViewerModel(scenario; tracer_count = 8, history_length = 3)
        presentation_model.presentation.diagnostic_interval = zero(scalar_type(scenario))
        wrapped = PresentationFailingSolver(presentation_model.solver, failure)
        presentation_model.solver = wrapped
        failed_presentation = update!(presentation_model)
        @test presentation_model.paused
        @test failed_presentation.phase == :failed
        @test presentation_model.solver === wrapped
        @test presentation_model.recovery_count == 0
        @test presentation_model.simulation_time == scenario.output_dt
        @test occursin("presentation error", failed_presentation.status)
    end
    model.presentation.diagnostic_interval = 10.0
    @test model.metrics_warming
    @test model.presentation.diagnostics.values["time"] == 0.0
    update!(model)
    @test !model.metrics_warming
    @test model.presentation.diagnostics.values["time"] == model.simulation_time

    model.tracers.positions .= reshape(
        repeat([scenario.domain.bounds[1][1], scenario.domain.bounds[2][1]], 32),
        2,
        32,
    )
    generations_before_reseed = copy(model.tracers.generations)
    reseeded = reseed_tracers!(
        model.tracers,
        scenario,
        model.geometry,
        control_at(scenario, 0).angle_degrees,
    )
    @test reseeded == size(model.tracers.positions, 2)
    @test model.tracers.generations == generations_before_reseed .+ 1
    @test all(isfinite, model.tracers.positions)

    update!(model)
    recovery_time = model.simulation_time
    stable = model.solver::StableFluidsSolver{Float64}
    @test adjust_tuning!(model, 0.05)
    stable.u[1, 1] = NaN
    model.last_requested_angular_velocity = 600.0
    recover_solver!(model, ArgumentError("injected finite-state failure"))
    @test model.recovery_count == 1
    @test model.simulation_time == recovery_time + scenario.output_dt
    @test all(isfinite, export_state(model.solver).velocity)
    @test occursin("fresh restart", model.status_message)
    @test !model.metrics_warming
    @test iszero(model.last_requested_angular_velocity)
    recovered_snapshot = snapshot(model)
    @test occursin("recovery_epoch=1", recovered_snapshot.status)
    @test recovered_snapshot.recovery_reason == model.recovery_reason
    @test recovered_snapshot.recovery_stage == model.recovery_stage
    @test stable_transport_mode(model.solver::StableFluidsSolver) == "skew-rk2"

    reset_viewer!(model)
    @test model.recovery_count == 1
    @test occursin("E=—  Ω=—  div=—  leak=—", snapshot(model).status)
    recover_solver!(model, ArgumentError("second injected finite-state failure"))
    @test model.recovery_count == 2
    @test occursin("recovery_epoch=2", snapshot(model).status)

    set_angle!(model, 0.0, 2.0)
    @test !rapid_drag_attempted(model)
    set_angle!(model, 30.0, 2.01)
    @test rapid_drag_attempted(model)
    enable_pose_only_drag!(model)
    @test model.pose_only_drag
    @test accepted(switch_solver!(model, "pic-flip"))
    @test model.pose_only_drag
    pose_control = ControlState(
        model.simulation_time + scenario.output_dt,
        something(model.manual_angle),
        0.0,
    )
    advance!(model.solver, pose_control, scenario.output_dt)
    model.simulation_time += scenario.output_dt
    @test export_state(model.solver).angular_velocity_degrees == 0.0
    release_angle!(model)
    update!(model)
    @test !model.pose_only_drag
    @test model.pose_only_guarded_trial

    float32_scenario = resized_scenario(
        load_scenario(joinpath(REPOSITORY_ROOT, "scenarios", "airfoil", "default.json")),
        (20, 12),
    )
    float32_worker = ViewerWorker(
        ViewerModel(float32_scenario; tracer_count = 16, history_length = 3),
    )
    drag_command = SetAngleCommand(12.0f0, 1.0)
    drag_sequence = enqueue!(float32_worker, drag_command)
    @test drag_sequence == 1
    @test float32_worker.latest_angle[] !== nothing
    @test something(float32_worker.latest_angle[]).command === drag_command
    tuning_command = AdjustTuningCommand(0.05f0)
    @test enqueue!(float32_worker, tuning_command) == 2

    worker_model = ViewerModel(scenario; tracer_count = 16, history_length = 3)
    worker = ViewerWorker(worker_model)
    start!(worker)
    first_snapshot = wait_for_snapshot(worker)
    failed_snapshot = FoilBenchJulia._failed_snapshot(first_snapshot, "injected")
    @test failed_snapshot.phase == :failed
    @test failed_snapshot.tracer_recycle_counters == first_snapshot.tracer_recycle_counters
    @test first_snapshot.solver_id == "stable-fluids"
    @test latest_snapshot(worker) === latest_snapshot(worker)
    pause_sequence = enqueue!(worker, TogglePauseCommand())
    paused_snapshot = wait_for_command(worker, pause_sequence)
    @test paused_snapshot.paused
    enqueue!(worker, SetAngleCommand(10.0, 1.0))
    enqueue!(worker, SwitchSolverCommand("pic-flip"))
    final_pose_sequence = enqueue!(worker, SetAngleCommand(20.0, 2.0))
    final_pose_snapshot = wait_for_command(worker, final_pose_sequence)
    @test final_pose_snapshot.angle_degrees == 20.0
    @test export_state(worker.model.solver).angle_degrees == 10.0
    close!(worker)
    @test worker.task === nothing
    @test something(latest_snapshot(worker)).applied_command > final_pose_sequence
    @test_throws ArgumentError enqueue!(worker, SetAngleCommand(0.0, 3.0))

    boundary_worker = ViewerWorker(
        ViewerModel(scenario; tracer_count = 16, history_length = 3),
    )
    switch_sequence = enqueue!(boundary_worker, SwitchSolverCommand("pic-flip"))
    start!(boundary_worker)
    switched_snapshot = wait_for_command(boundary_worker, switch_sequence)
    @test switched_snapshot.time ≈ scenario.output_dt
    @test switched_snapshot.solver_id == "pic-flip"
    close!(boundary_worker)

    failing_worker = ViewerWorker(
        ViewerModel(scenario; tracer_count = 16, history_length = 3),
    )
    failure_sequence = enqueue!(failing_worker, FailingViewerCommand())
    start!(failing_worker)
    failed_command = wait_for_command(failing_worker, failure_sequence)
    @test failed_command.paused
    @test occursin("worker command error", failed_command.status)
    close!(failing_worker)
    @test failing_worker.task === nothing

    concurrent_worker = ViewerWorker(
        ViewerModel(scenario; tracer_count = 8, history_length = 3),
    )
    producers = [
        Threads.@spawn begin
            last_sequence = UInt64(0)
            for _ in 1:100
                last_sequence = enqueue!(concurrent_worker, ToggleCropCommand())
            end
            last_sequence
        end for _ in 1:4
    ]
    @test timedwait(() -> all(istaskdone, producers), 5.0) == :ok
    final_concurrent_sequence = maximum(fetch.(producers))
    start!(concurrent_worker)
    concurrent_snapshot = wait_for_command(
        concurrent_worker,
        final_concurrent_sequence;
        timeout = 20.0,
    )
    @test concurrent_snapshot.applied_command == final_concurrent_sequence
    close!(concurrent_worker)
    @test concurrent_worker.task === nothing

    for solver_id in ("stable-fluids", "unavailable-solver")
        evidence_worker = ViewerWorker(
            ViewerModel(scenario; tracer_count = 8, history_length = 3),
        )
        evidence_worker.recovery_pending = true
        append!(evidence_worker.recent_failures, (1.0, 2.0))
        @test FoilBenchJulia._apply_command!(
            evidence_worker,
            SwitchSolverCommand(solver_id),
        )
        @test evidence_worker.recovery_pending
        @test evidence_worker.recent_failures == [1.0, 2.0]
        @test solver_info(evidence_worker.model.solver).id == "stable-fluids"
    end

    paced_worker = ViewerWorker(
        ViewerModel(scenario; tracer_count = 8, history_length = 3),
    )
    pacing_started = time()
    start!(paced_worker)
    previous_paced_time = 0.0
    for sample in 2:7
        sequence = enqueue!(
            paced_worker,
            SetAngleCommand(Float64(sample), Float64(sample)),
        )
        paced_snapshot = wait_for_command(paced_worker, sequence)
        @test paced_snapshot.time > previous_paced_time
        previous_paced_time = paced_snapshot.time
    end
    pacing_elapsed = time() - pacing_started
    @test pacing_elapsed >= 0.07
    @test paced_worker.model.simulated_seconds_per_wall_second <= 2.0
    close!(paced_worker)
end

@testset "All directed Julia warm swaps" begin
    scenario = resized_scenario(
        load_scenario(joinpath(REPOSITORY_ROOT, "scenarios", "airfoil", "default.json")),
        (20, 12),
    )
    solver_ids = ("stable-fluids", "lbm-d2q9", "pic-flip")
    for angle in (14.0f0, 25.0f0), source in solver_ids, destination in solver_ids
        source == destination && continue
        model = ViewerModel(
            scenario;
            solver_id = source,
            tracer_count = 12,
            history_length = 3,
        )
        set_angle!(model, angle, 1.0)
        @test accepted(switch_solver!(model, destination))
        @test solver_info(model.solver).id == destination
        @test model.manual_angle == angle
        @test model.simulation_time == scenario.output_dt
        @test all(isfinite, export_state(model.solver).velocity)
        release_angle!(model)
        updated = update!(model)
        @test updated.time == 2 * scenario.output_dt
        @test all(isfinite, export_state(model.solver).velocity)
    end
end

@testset "Shared viewer transcript" begin
    transcript_path = joinpath(REPOSITORY_ROOT, "spec", "conformance", "viewer-basic.json")
    transcript = JSON3.read(read(transcript_path, String))
    scenario = load_scenario(joinpath(REPOSITORY_ROOT, String(transcript.scenario)))
    model = ViewerModel(scenario; solver_id = String(transcript.solver))
    stopped = false
    last_sequence = 0
    for action in transcript.actions
        sequence = Int(action.sequence)
        @test sequence > last_sequence
        last_sequence = sequence
        previous_time = model.simulation_time
        kind = String(action.kind)
        if kind == "step"
            update!(model)
        elseif kind == "pause"
            toggle_pause!(model)
        elseif kind == "reset"
            reset_viewer!(model)
        elseif kind == "set-angle"
            set_angle!(model, Float64(action.angle_degrees), Float64(action.at))
        elseif kind == "release-angle"
            release_angle!(model)
        elseif kind == "switch"
            switch_solver!(model, String(action.solver))
        elseif kind == "set-reynolds"
            set_reynolds!(model, Float64(action.reynolds))
        elseif kind == "toggle-diagnostics"
            toggle_diagnostics!(model)
        elseif kind == "shutdown"
            stopped = true
        else
            error("unsupported transcript action: $kind")
        end
        expected = action.expect
        state = viewer_session_state(model)
        if haskey(expected, :phase)
            @test String(stopped ? :stopped : state.phase) == String(expected.phase)
        end
        if haskey(expected, :motion_mode)
            @test String(state.motion_mode) == String(expected.motion_mode)
        end
        if haskey(expected, :diagnostic_mode)
            @test String(state.diagnostic_mode) == String(expected.diagnostic_mode)
        end
        if haskey(expected, :schedule_active)
            @test state.schedule_active == Bool(expected.schedule_active)
        end
        if haskey(expected, :angle_degrees)
            angle = something(model.manual_angle, control_at(model.scenario, model.simulation_time).angle_degrees)
            @test angle ≈ Float64(expected.angle_degrees)
        end
        if haskey(expected, :time_relation)
            relation = String(expected.time_relation)
            if relation == "advanced"
                @test model.simulation_time > previous_time
            elseif relation == "unchanged"
                @test model.simulation_time == previous_time
            elseif relation == "reset"
                @test model.simulation_time == 0
            end
        end
    end
end

@testset "Julia benchmark contracts" begin
    matrix = load_benchmark_matrix(joinpath(REPOSITORY_ROOT, "benchmark-matrices", "test.json"))
    @test matrix.id == "test"
    @test matrix.solvers == collect(solver_ids())
    @test matrix.resolutions == [(32, 16)]
    selected_root = replace(normpath(find_repository_root(matrix.scenario_path)), r"[\\/]+$" => "")
    expected_root = replace(normpath(REPOSITORY_ROOT), r"[\\/]+$" => "")
    @test selected_root == expected_root
    description = describe_implementation()
    @test description["implementation"] == "julia"
    @test length(description["solvers"]) == 3
    for solver_id in solver_ids()
        @test solver_info(create_solver(solver_id, Float64)).id == solver_id
    end

    result = Dict{String,Any}(
        "schema_version" => 1,
        "contract_id" => "foilbench-phase2-v1",
        "contract_revision" => 4,
        "benchmark_matrix_id" => "test",
        "scenario_id" => "test",
        "repetition" => 1,
        "language" => "julia",
        "solver" => "stable-fluids",
        "git_commit" => "test",
        "machine" => Dict{String,Any}(),
        "precision" => "float64",
        "resolution" => [16, 8],
        "bounds" => [[-1.0, 1.0], [-0.5, 0.5]],
        "periodic_axes" => ["x"],
        "reynolds" => 100.0,
        "effective_reynolds" => 100.0,
        "solver_configuration" => Dict{String,Any}(
            "initial_condition" => "freestream",
            "stable_advection" => "maccormack",
            "stable_face_advection" => false,
            "stable_cfl" => 0.7,
            "pressure_tolerance" => 1.0e-5,
            "pressure_max_iterations" => 640,
            "pic_flip_blend" => 0.95,
            "pic_population_interval" => 8,
            "pic_cfl" => 0.75,
        ),
        "freestream" => [1.0, 0.0],
        "foil" => Dict("naca" => "0012", "chord" => 1.0, "pivot" => [0.0, 0.0]),
        "control_history" => [Dict("time" => 0.0, "angle_degrees" => 0.0)],
        "requested_duration" => 0.1,
        "simulated_duration" => 0.1,
        "output_dt" => 0.01,
        "seed" => 0,
        "initialization_seconds" => 0.1,
        "cold_step_seconds" => 0.2,
        "step_seconds" => [0.01, 0.02],
        "median_step_seconds" => 0.015,
        "p95_step_seconds" => 0.0195,
        "simulated_seconds_per_wall_second" => 0.1 / 0.03,
        "cell_updates_per_second" => 16 * 8 * 2 / 0.03,
        "particle_updates_per_second" => 0.0,
        "peak_rss_bytes" => 1,
        "memory_measurement" => "rss",
        "runtime_startup_seconds" => nothing,
        "worker_startup_seconds" => nothing,
        "substeps" => 2,
        "final_state_revision" => 1,
        "diagnostic_state_revision" => 1,
        "last_step" => Dict{String,Any}(
            "requested_dt" => 0.01,
            "advanced_dt" => 0.01,
            "substeps" => 1,
            "max_speed" => 1.0,
            "state_revision" => 1,
            "evidence" => Dict("maximum_fluid_speed" => 1.0),
            "warnings" => String[],
        ),
        "diagnostics" => Dict{String,Float64}(),
        "success" => true,
        "failure" => nothing,
        "warnings" => String[],
    )
    schema_path = joinpath(REPOSITORY_ROOT, "spec", "schemas", "result.schema.json")
    revision5_schema_path = joinpath(
        REPOSITORY_ROOT, "spec", "schemas", "result-v2.schema.json",
    )
    @test isnothing(validate_benchmark_result(result, schema_path))
    equivalent = deepcopy(result)
    equivalent["language"] = "typescript"
    equivalent["reynolds"] = 100
    equivalent["foil"] = Dict("pivot" => [0.0, 0.0], "chord" => 1, "naca" => "0012")
    @test isnothing(validate_benchmark_result(equivalent, schema_path))
    stale = deepcopy(result)
    stale["diagnostic_state_revision"] = 0
    @test_throws ArgumentError validate_benchmark_result(stale, schema_path)
    inconsistent = deepcopy(result)
    inconsistent["median_step_seconds"] = 123.0
    @test_throws ArgumentError validate_benchmark_result(inconsistent, schema_path)
    invalid = copy(result)
    invalid["unexpected"] = true
    @test_throws ArgumentError validate_benchmark_result(invalid, schema_path)
    for (field, value) in (
        ("scenario_id", 7),
        ("resolution", [16.5, 8]),
        ("resolution", [3, 8]),
        ("peak_rss_bytes", 1.5),
        ("diagnostics", Any[]),
        ("warnings", Any[7]),
    )
        malformed = copy(result)
        malformed[field] = value
        @test_throws ArgumentError validate_benchmark_result(malformed, schema_path)
    end

    cartesian_results = [Dict{String,Any}(
        "benchmark_matrix_id" => "test",
        "language" => "julia",
        "solver" => solver_id,
        "resolution" => [32, 16],
        "repetition" => 1,
        "success" => true,
    ) for solver_id in solver_ids()]
    @test_throws ArgumentError FoilBenchJulia._assert_complete_benchmark_matrices(
        cartesian_results,
        ["julia", "python"],
    )
    cartesian_results[1]["success"] = false
    @test_throws ArgumentError FoilBenchJulia._assert_complete_benchmark_matrices(
        cartesian_results,
        ["julia"],
    )

    mktempdir() do directory
        open(joinpath(directory, "result.json"), "w") do io
            JSON3.pretty(io, result)
        end
        rounded = deepcopy(result)
        rounded["language"] = "typescript"
        rounded["output_dt"] = Float64(result["output_dt"]) * (1 + 5.0e-13)
        rounded["effective_reynolds"] = Float64(result["effective_reynolds"]) * (1 - 5.0e-13)
        open(joinpath(directory, "rounded.json"), "w") do io
            JSON3.pretty(io, rounded)
        end
        @test length(collect_benchmark_results(directory)) == 2
        comparison = format_benchmark_comparison(directory)
        @test occursin("stable-fluids", comparison)
        @test occursin("julia", comparison)
        rounded["output_dt"] = Float64(result["output_dt"]) * 1.01
        open(joinpath(directory, "rounded.json"), "w") do io
            JSON3.pretty(io, rounded)
        end
        @test_throws ArgumentError format_benchmark_comparison(directory)
    end

    mktempdir() do directory
        @test_throws ArgumentError format_benchmark_comparison(
            directory;
            require_complete = true,
            required_languages = ["python", "julia", "typescript"],
        )
    end

    mktempdir() do directory
        output = run_benchmark_matrix(
            joinpath(REPOSITORY_ROOT, "benchmark-matrices", "test.json"),
            directory,
        )
        artifacts = filter(name -> endswith(name, ".json"), readdir(output))
        @test length(artifacts) == length(solver_ids())
        for artifact in artifacts
            document = JSON3.read(
                read(joinpath(output, artifact), String),
                Dict{String,Any},
            )
            @test document["periodic_axes"] isa Vector
            @test isnothing(validate_benchmark_result(document, revision5_schema_path))
            @test document["contract_id"] == "foilbench-phase3-v1"
            @test document["contract_revision"] == 5
            @test document["implementation"] == "julia"
            @test document["execution_target"] == "native"
            @test isnothing(document["failure"])
            @test document["final_state_revision"] == document["diagnostic_state_revision"]
            @test document["final_state_revision"] == document["last_step"]["state_revision"]
            @test document["diagnostics"]["wake_probe_samples"] >= 8
        end
        @test occursin(
            "stable-fluids",
            format_benchmark_comparison(output; require_complete = true),
        )
        @test occursin(
            "stable-fluids",
            format_benchmark_comparison(output; required_languages = ["julia"]),
        )
        @test occursin(
            "stable-fluids",
            format_benchmark_comparison(output; required_producers = ["julia/native"]),
        )
        @test_throws ArgumentError format_benchmark_comparison(
            output;
            required_languages = ["python", "julia"],
        )
        @test_throws ArgumentError format_benchmark_comparison(
            output;
            required_producers = ["julia/native", "rust/native"],
        )
        rm(joinpath(output, first(artifacts)))
        @test_throws ArgumentError format_benchmark_comparison(
            output;
            require_complete = true,
        )
    end
end

@testset "Julia chaotic-wake experiments" begin
    coherent = [sin(2pi * index / 32) for index in 0:127]
    multiscale = [
        sin(2pi * index / 32) + 0.35 * sin(2pi * index / 11) +
            0.2 * sin(2pi * index / 7)
        for index in 0:127
    ]
    coherent_spectrum = temporal_spectral_statistics(coherent)
    multiscale_spectrum = temporal_spectral_statistics(multiscale)
    @test multiscale_spectrum.entropy > coherent_spectrum.entropy
    @test multiscale_spectrum.broadband_power_fraction >
          coherent_spectrum.broadband_power_fraction

    base = load_scenario(
        joinpath(REPOSITORY_ROOT, "scenarios", "airfoil", "chaotic-experimental.json"),
    )
    selected = WakeSweepCase(10_000.0, 35.0, (24, 14))
    scenario = chaotic_scenario(base, selected, 0.1)
    @test scenario.solver_options["stable_advection"] == "skew-rk2"
    @test scenario.controls[1].angle_degrees == 35.0f0
    wake = run_chaotic_wake_case(base, selected; duration = 0.1, burn_in = 0.03)
    @test wake["spectral_entropy"] >= 0
    @test wake["enstrophy_coefficient_of_variation"] >= 0
    @test wake["vorticity_small_scale_fraction"] >= 0
    sensitivity = run_chaos_sensitivity(base, selected; duration = 0.1, epsilon = 1.0e-4)
    @test sensitivity["initial_wake_rms_difference"] > 0
    @test sensitivity["initialization"]["reference_import_status"] == "accepted"
    @test sensitivity["initialization"]["perturbed_import_status"] == "accepted"
    @test sensitivity["initialization"]["authoritative_angle_degrees"] ==
          selected.angle_degrees
    @test sensitivity["amplification"] > 0
    @test length(sensitivity["times"]) == length(sensitivity["wake_rms_differences"])
    @test all(isfinite, sensitivity["wake_rms_differences"])
end

@testset "Matched canonical fidelity cases" begin
    fidelity_fixture = JSON3.read(
        read(joinpath(FIXTURES, "fidelity-cases.json"), String),
    )
    fidelity_cases = Dict(String(case.id) => case for case in fidelity_fixture.cases)
    for solver_id in solver_ids()
        uniform_case = fidelity_cases["uniform"]
        uniform = scenario_with_run(
            load_scenario(joinpath(REPOSITORY_ROOT, String(uniform_case.scenario))),
            Tuple(Int.(uniform_case.resolution)),
            Float64(uniform_case.duration),
        )
        solver = create_solver(solver_id, Float64)
        initialize!(solver, uniform, NacaFoil(uniform.foil), 0)
        before = cell_velocity(solver)
        for step in 1:round(Int, uniform.duration / uniform.output_dt)
            advance!(solver, control_at(uniform, step * uniform.output_dt), uniform.output_dt)
        end
        after = cell_velocity(solver)
        @test sqrt(sum(abs2, after .- before) / length(after)) <
              uniform_case.metrics.velocity_rms_drift.threshold
        @test sqrt(sum(abs2, vorticity(after, uniform.domain)) / prod(size(after)[1:2])) <
              uniform_case.metrics.spurious_vorticity_rms.threshold

        taylor_case = fidelity_cases["taylor-green"]
        taylor_base = load_scenario(
            joinpath(REPOSITORY_ROOT, String(taylor_case.scenario)),
        )
        taylor = scenario_with_run(
            taylor_base,
            Tuple(Int.(taylor_case.resolution)),
            Float64(taylor_case.duration),
        )
        solver = create_solver(solver_id, Float64)
        initialize!(solver, taylor, NacaFoil(taylor.foil), 0)
        initial_energy = diagnostics(solver).values["kinetic_energy"]
        for step in 1:round(Int, taylor.duration / taylor.output_dt)
            advance!(solver, control_at(taylor, step * taylor.output_dt), taylor.output_dt)
        end
        centers = cell_centers(taylor.domain)
        expected = Array{Float64,3}(
            undef,
            taylor.domain.resolution[1],
            taylor.domain.resolution[2],
            2,
        )
        decay = exp(-2 * reference_speed(taylor) * taylor.foil.chord /
            taylor.reynolds * taylor.duration)
        for j in axes(expected, 2), i in axes(expected, 1)
            x, y = centers[i, j, 1], centers[i, j, 2]
            expected[i, j, 1] = sin(x) * cos(y) * decay
            expected[i, j, 2] = -cos(x) * sin(y) * decay
        end
        actual = cell_velocity(solver)
        @test sqrt(sum(abs2, actual .- expected) / length(actual)) <
              taylor_case.metrics.velocity_l2_error.threshold
        @test 0 <= diagnostics(solver).values["kinetic_energy"] <=
              taylor_case.metrics.kinetic_energy_ratio.threshold * initial_energy

        poiseuille_case = fidelity_cases["poiseuille"]
        poiseuille_base = load_scenario(
            joinpath(REPOSITORY_ROOT, String(poiseuille_case.scenario)),
        )
        poiseuille = scenario_with_run(
            poiseuille_base,
            Tuple(Int.(poiseuille_case.resolution)),
            Float64(poiseuille_case.duration),
        )
        solver = create_solver(solver_id, Float64)
        initialize!(solver, poiseuille, NacaFoil(poiseuille.foil), 0)
        for step in 1:round(Int, poiseuille.duration / poiseuille.output_dt)
            advance!(solver, control_at(poiseuille, step * poiseuille.output_dt), poiseuille.output_dt)
        end
        velocity = cell_velocity(solver)
        centers = cell_centers(poiseuille.domain)
        y0, y1 = poiseuille.domain.bounds[2]
        radius = 0.5 * (y1 - y0)
        center_y = 0.5 * (y0 + y1)
        profile_error = 0.0
        for j in axes(velocity, 2), i in axes(velocity, 1)
            expected_u = 1.5 * (1 - ((centers[i, j, 2] - center_y) / radius)^2)
            profile_error += (velocity[i, j, 1] - expected_u)^2
        end
        profile_error = sqrt(profile_error / (size(velocity, 1) * size(velocity, 2)))
        center_speed = sum(view(velocity, :, cld(size(velocity, 2), 2), 1)) / size(velocity, 1)
        top_row = size(velocity, 2)
        wall_speed = 0.5 * (sum(view(velocity, :, 1, 1)) +
            sum(view(velocity, :, top_row, 1))) /
            size(velocity, 1)
        normal_leakage = max(maximum(abs, view(velocity, :, 1, 2)),
            maximum(abs, view(velocity, :, top_row, 2)))
        @test center_speed > wall_speed
        @test profile_error < poiseuille_case.metrics.profile_l2_error.threshold
        @test normal_leakage < poiseuille_case.metrics.normal_wall_leakage.threshold

        naca_case = fidelity_cases["naca0012-zero"]
        naca_base = load_scenario(
            joinpath(REPOSITORY_ROOT, String(naca_case.scenario)),
        )
        naca = scenario_with_run(
            naca_base,
            Tuple(Int.(naca_case.resolution)),
            Float64(naca_case.duration),
        )
        solver = create_solver(solver_id, Float32)
        initialize!(solver, naca, NacaFoil(naca.foil), naca.seed)
        for step in 1:round(Int, naca.duration / naca.output_dt)
            advance!(solver, control_at(naca, step * naca.output_dt), naca.output_dt)
        end
        velocity = cell_velocity(solver)
        symmetry_error = 0.0f0
        for component in 1:2, j in axes(velocity, 2), i in axes(velocity, 1)
            mirror = size(velocity, 2) - j + 1
            delta = component == 1 ? velocity[i, j, component] - velocity[i, mirror, component] :
                velocity[i, j, component] + velocity[i, mirror, component]
            symmetry_error += delta^2
        end
        @test sqrt(symmetry_error / length(velocity)) <
              naca_case.metrics.symmetry_l2_error.threshold
        @test diagnostics(solver).values["solid_leakage"] <
              naca_case.metrics.solid_leakage.threshold

        dynamic_case = fidelity_cases["naca2412-dynamic"]
        dynamic_base = load_scenario(
            joinpath(REPOSITORY_ROOT, String(dynamic_case.scenario)),
        )
        dynamic = scenario_with_run(
            dynamic_base,
            Tuple(Int.(dynamic_case.resolution)),
            Float64(dynamic_case.duration),
        )
        solver = create_solver(solver_id, Float32)
        initialize!(solver, dynamic, NacaFoil(dynamic.foil), dynamic.seed)
        for step in 1:round(Int, dynamic.duration / dynamic.output_dt)
            advance!(solver, control_at(dynamic, step * dynamic.output_dt), dynamic.output_dt)
        end
        dynamic_diagnostics = diagnostics(solver).values
        for name in String.(propertynames(dynamic_case.metrics))
            @test haskey(dynamic_diagnostics, name)
            @test isfinite(dynamic_diagnostics[name])
        end
    end
end
