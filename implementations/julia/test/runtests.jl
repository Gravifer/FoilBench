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
    iterations, converged = project_faces!(
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
    @test iterations > 0
    @test after < 0.5 * before

    impulse = zeros(Float64, 24, 16)
    impulse[12, 8] = 1.0
    diffused, diffusion_iterations, diffusion_converged = implicit_diffuse_scalar(
        impulse,
        0.1,
        0.02,
        domain;
        tolerance = 1.0e-8,
    )
    @test diffusion_converged
    @test diffusion_iterations > 0
    @test 0.0 < maximum(diffused) < 1.0
    @test sum(diffused) ≈ 1.0 atol = 1.0e-7
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
    import_report = import_state!(imported, state, control_at(uniform, state.time))
    @test import_report.source_solver == "stable-fluids"
    @test import_report.destination_solver == "stable-fluids"
    @test cell_velocity(imported) ≈ cell_velocity(solver) atol = 1.0e-10
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
    import_report = import_state!(imported, state, control_at(uniform, state.time))
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
    import_report = import_state!(imported, state, control_at(uniform, state.time))
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

    chaotic = resized_scenario(
        load_scenario(
            joinpath(REPOSITORY_ROOT, "scenarios", "airfoil", "chaotic-experimental.json"),
        ),
        (40, 24),
    )
    chaotic_solver = StableFluidsSolver(Float32)
    initialize!(chaotic_solver, chaotic, NacaFoil(chaotic.foil), 0)
    @test chaotic_solver.skew_rk2
    chaotic_report = advance!(
        chaotic_solver,
        control_at(chaotic, chaotic.output_dt),
        chaotic.output_dt,
    )
    @test chaotic_report.advanced_dt == chaotic.output_dt
    @test all(isfinite, export_state(chaotic_solver).velocity)
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
    @test size(path_segments(model.tracers), 2) == 2 * 32 * 4 - 2

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
    rejected_solver = rejected_model.solver::StableFluidsSolver{Float64}
    rejected_solver.u[1, 1] = NaN
    rejected = switch_solver!(rejected_model, "lbm-d2q9")
    @test !accepted(rejected)
    @test rejected.reason == :nonfinite_state
    @test solver_info(rejected_model.solver).id == "stable-fluids"

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
    set_angle!(model, 13.0, 1.1)
    @test model.manual_angle == 13.0
    @test 0 < requested_tip_speed_ratio(model) <= 8
    release_angle!(model)
    @test model.angular_velocity == 0.0
    adjust_reynolds!(model, 1.0)
    @test reynolds(model.solver) ≈ 10 * scenario.reynolds
    @test model.playback_rate ≈ 1.5
    reset_reynolds!(model)
    @test reynolds(model.solver) == scenario.reynolds
    @test toggle_tracer_mode!(model) == :material
    @test !toggle_vorticity!(model)
    toggled = snapshot(model)
    @test occursin("tracers=material", toggled.status)
    @test occursin("vort=off", toggled.status)
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
    @test stable_transport_mode(model.solver::StableFluidsSolver) == "maccormack"

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
    set_stable_transport_mode!(stable, "skew-rk2")
    model.stable_transport = "skew-rk2"
    stable.u[1, 1] = NaN
    recover_solver!(model, ArgumentError("injected finite-state failure"))
    @test model.recovery_count == 1
    @test model.simulation_time == recovery_time
    @test all(isfinite, export_state(model.solver).velocity)
    @test occursin("fresh restart", model.status_message)
    @test model.metrics_warming
    @test occursin("recovery_epoch=1", snapshot(model).status)
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
end

@testset "All directed Julia warm swaps" begin
    scenario = resized_scenario(
        load_scenario(joinpath(REPOSITORY_ROOT, "scenarios", "airfoil", "default.json")),
        (20, 12),
    )
    solver_ids = ("stable-fluids", "lbm-d2q9", "pic-flip")
    for angle in (4.0f0, 25.0f0), source in solver_ids, destination in solver_ids
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
        @test all(isfinite, updated.velocity)
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
        "scenario_id" => "test",
        "language" => "julia",
        "solver" => "stable-fluids",
        "git_commit" => "test",
        "machine" => Dict{String,Any}(),
        "precision" => "float64",
        "resolution" => [16, 8],
        "seed" => 0,
        "initialization_seconds" => 0.1,
        "cold_step_seconds" => 0.2,
        "step_seconds" => [0.01, 0.02],
        "median_step_seconds" => 0.015,
        "p95_step_seconds" => 0.02,
        "simulated_seconds_per_wall_second" => 1.0,
        "cell_updates_per_second" => 10.0,
        "particle_updates_per_second" => 0.0,
        "peak_rss_bytes" => 1,
        "substeps" => 2,
        "diagnostics" => Dict{String,Float64}(),
        "success" => true,
        "warnings" => String[],
    )
    schema_path = joinpath(REPOSITORY_ROOT, "spec", "result.schema.json")
    @test isnothing(validate_benchmark_result(result, schema_path))
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

    mktempdir() do directory
        open(joinpath(directory, "result.json"), "w") do io
            JSON3.pretty(io, result)
        end
        @test length(collect_benchmark_results(directory)) == 1
        comparison = format_benchmark_comparison(directory)
        @test occursin("stable-fluids", comparison)
        @test occursin("julia", comparison)
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
    @test sensitivity["amplification"] > 0
    @test length(sensitivity["times"]) == length(sensitivity["wake_rms_differences"])
    @test all(isfinite, sensitivity["wake_rms_differences"])
end

@testset "Matched canonical fidelity cases" begin
    for solver_id in solver_ids()
        uniform = resized_scenario(
            load_scenario(joinpath(REPOSITORY_ROOT, "scenarios", "validation", "uniform.json")),
            (32, 16),
        )
        solver = create_solver(solver_id, Float64)
        initialize!(solver, uniform, NacaFoil(uniform.foil), 0)
        before = cell_velocity(solver)
        for step in 1:5
            advance!(solver, control_at(uniform, step * uniform.output_dt), uniform.output_dt)
        end
        after = cell_velocity(solver)
        @test sqrt(sum(abs2, after .- before) / length(after)) < 1.0e-5
        @test sqrt(sum(abs2, vorticity(after, uniform.domain)) / prod(size(after)[1:2])) < 1.0e-5

        taylor_base = load_scenario(
            joinpath(REPOSITORY_ROOT, "scenarios", "validation", "taylor-green.json"),
        )
        taylor = scenario_with_run(taylor_base, (32, 32), 0.1)
        solver = create_solver(solver_id, Float64)
        initialize!(solver, taylor, NacaFoil(taylor.foil), 0)
        initial_energy = diagnostics(solver).values["kinetic_energy"]
        for step in 1:5
            advance!(solver, control_at(taylor, step * taylor.output_dt), taylor.output_dt)
        end
        centers = cell_centers(taylor.domain)
        expected = Array{Float64,3}(undef, 32, 32, 2)
        decay = exp(-2 * reference_speed(taylor) * taylor.foil.chord /
            taylor.reynolds * taylor.duration)
        for j in 1:32, i in 1:32
            x, y = centers[i, j, 1], centers[i, j, 2]
            expected[i, j, 1] = sin(x) * cos(y) * decay
            expected[i, j, 2] = -cos(x) * sin(y) * decay
        end
        actual = cell_velocity(solver)
        @test sqrt(sum(abs2, actual .- expected) / length(actual)) < 0.08
        @test 0 <= diagnostics(solver).values["kinetic_energy"] <= 1.01 * initial_energy

        poiseuille_base = load_scenario(
            joinpath(REPOSITORY_ROOT, "scenarios", "validation", "poiseuille.json"),
        )
        poiseuille = scenario_with_run(poiseuille_base, (32, 16), 0.1)
        solver = create_solver(solver_id, Float64)
        initialize!(solver, poiseuille, NacaFoil(poiseuille.foil), 0)
        for step in 1:5
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
        @test profile_error < 0.25
        @test normal_leakage < 0.01

        naca_base = load_scenario(
            joinpath(REPOSITORY_ROOT, "scenarios", "validation", "naca0012-zero.json"),
        )
        naca = scenario_with_run(naca_base, (40, 24), 0.1)
        solver = create_solver(solver_id, Float32)
        initialize!(solver, naca, NacaFoil(naca.foil), naca.seed)
        for step in 1:6
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
        @test sqrt(symmetry_error / length(velocity)) < 0.01
        @test diagnostics(solver).values["solid_leakage"] < 1.0e-6

        dynamic_base = load_scenario(
            joinpath(REPOSITORY_ROOT, "scenarios", "airfoil", "default.json"),
        )
        dynamic = scenario_with_run(dynamic_base, (32, 20), 0.05)
        solver = create_solver(solver_id, Float32)
        initialize!(solver, dynamic, NacaFoil(dynamic.foil), dynamic.seed)
        for step in 1:3
            advance!(solver, control_at(dynamic, step * dynamic.output_dt), dynamic.output_dt)
        end
        dynamic_diagnostics = diagnostics(solver).values
        for name in ("wake_width", "recirculation_area", "enstrophy", "solid_leakage")
            @test haskey(dynamic_diagnostics, name)
            @test isfinite(dynamic_diagnostics[name])
        end
    end
end
