# FoilBench development shortcuts. Run `just` to list them.
set windows-shell := ["pwsh", "-NoLogo", "-NoProfile", "-Command"]

# List the available shortcuts.
default:
    @just --list

# Install every currently implemented language environment.
setup: py-setup jl-setup ts-setup rs-setup

# Install the Python reference and development dependencies.
py-setup:
    uv sync --project implementations/python --all-groups

# Instantiate both the Julia solver and GLMakie viewer environments.
jl-setup:
    julia --project=implementations/julia -e 'using Pkg; Pkg.instantiate()'
    julia --project=implementations/julia/viewer -e 'using Pkg; Pkg.instantiate()'

# Install the TypeScript implementation.
ts-setup:
    @$client = [System.Net.Sockets.TcpClient]::new(); $viewerRunning = $false; try { $client.Connect('127.0.0.1', 4173); $viewerRunning = $true } catch [System.Net.Sockets.SocketException] { } finally { $client.Dispose() }; if ($viewerRunning) { throw 'FoilBench TypeScript viewer is running on port 4173. Stop ts-view before ts-setup so npm ci can replace native modules on Windows.' }
    npm --prefix implementations/typescript ci
    npm --prefix implementations/typescript run setup:browser

# Fetch and build the Rust Phase 3 native and WASM workspace.
rs-setup:
    rustup target add wasm32-unknown-unknown
    cargo fetch --manifest-path implementations/rust/Cargo.toml --locked
    cargo build --manifest-path implementations/rust/Cargo.toml --workspace --locked
    cargo check --manifest-path implementations/rust/Cargo.toml --locked -p foilbench-wasm --target wasm32-unknown-unknown

# Validate the contract manifest, specification layout, schemas, and acceptance fixtures.
verify-spec:
    uv run --project implementations/python python tools/validate_spec.py
    uv run --project implementations/python python tools/validate_acceptance_fixtures.py

# Run all implemented language checks, including Rust native and WASM compilation.
verify:
    pwsh -NoProfile -File tools/verify.ps1

# Run all checks plus all representative 160x96 acceptance gates.
verify-representative:
    pwsh -NoProfile -File tools/verify.ps1 -Representative

# Run only the Revision 5 cross-target artifact and chaos aggregate.
revision5-acceptance:
    pwsh -NoProfile -File tools/revision5_acceptance.ps1

# Generate the one-shot Revision 5 scheduled mixing/recovery evidence roster.
scheduled-fidelity:
    pwsh -NoProfile -File tools/run_scheduled_fidelity.ps1

# Run only the Python checks.
verify-python:
    pwsh -NoProfile -File tools/verify.ps1 -Python

# Run only the Julia checks.
verify-julia:
    pwsh -NoProfile -File tools/verify.ps1 -Julia

# Run only the TypeScript checks.
verify-typescript:
    pwsh -NoProfile -File tools/verify.ps1 -TypeScript

# Run Rust formatting, lint, and foundation tests.
verify-rust:
    pwsh -NoProfile -File tools/verify.ps1 -Rust

# Describe the current Rust solver repertoire.
rs-describe:
    cargo run --quiet --manifest-path implementations/rust/Cargo.toml --locked -p foilbench-native -- describe

# Run a native Rust benchmark matrix.
rs-bench matrix="benchmark-matrices/smoke.json":
    cargo run --quiet --manifest-path implementations/rust/Cargo.toml --locked -p foilbench-native -- bench "{{ matrix }}"

# Compare native Rust result artifacts.
rs-compare results="results/rust":
    cargo run --quiet --manifest-path implementations/rust/Cargo.toml --locked -p foilbench-native -- compare "{{ results }}"

# Open the Python viewer.
py-view scenario="scenarios/airfoil/default.json" solver="stable-fluids":
    uv run --project implementations/python foilbench-py view "{{ scenario }}" --solver "{{ solver }}"

# Open the Julia viewer.
jl-view scenario="scenarios/airfoil/default.json" solver="stable-fluids":
    julia --threads=auto --project=implementations/julia/viewer implementations/julia/bin/foilbench-jl view "{{ scenario }}" --solver "{{ solver }}"

# Open the Three.js development viewer with the TypeScript or Rust/WASM backend.
ts-view scenario="scenarios/airfoil/default.json" solver="stable-fluids" backend="typescript":
    npm --prefix implementations/typescript run view -- "{{ scenario }}" "{{ solver }}" "{{ backend }}"

# Open the polished static FoilBench browser lab.
web-view:
    npm --prefix implementations/typescript run web

# Build the GitHub Pages-shaped static browser lab without deploying it.
web-build:
    npm --prefix implementations/typescript run build:web:pages

# Preview the most recent static browser-lab build.
web-preview:
    npm --prefix implementations/typescript run web:preview

# Test the production static browser lab.
web-test:
    npm --prefix implementations/typescript run test:web:dist

# Run a TypeScript Chromium benchmark matrix.
ts-bench matrix="benchmark-matrices/smoke.json":
    npm --prefix implementations/typescript run bench -- "{{ matrix }}"

# Run the TypeScript 160x96 double-digit warmed-step acceptance gate.
ts-preview-gate:
    npm --prefix implementations/typescript run gate:preview

# Run the development-machine Rust/WASM 160x96 preview gate.
rs-wasm-preview-gate:
    npm --prefix implementations/typescript run gate:rust-wasm-preview

# Run the Python 160x96 double-digit warmed-step acceptance gate.
py-preview-gate:
    uv run --project implementations/python python implementations/python/benchmark/preview_gate.py

# Run the Python Revision 4 full-resolution directed warm-switch gate.
py-switch-gate:
    uv run --project implementations/python python implementations/python/benchmark/warm_switch_gate.py

# Run the Julia Revision 4 full-resolution directed warm-switch gate.
jl-switch-gate:
    julia --threads=auto --project=implementations/julia implementations/julia/benchmark/warm_switch_gate.jl

# Run the TypeScript Revision 4 full-resolution directed warm-switch gate.
ts-switch-gate:
    npm --prefix implementations/typescript run gate:warm-switch

# Run Python's full 160x96 startup acceptance gate.
py-startup-gate:
    uv run --project implementations/python python implementations/python/benchmark/startup_gate.py

# Run Julia's full 160x96 startup acceptance gate.
jl-startup-gate:
    julia --threads=auto --project=implementations/julia implementations/julia/benchmark/startup_gate.jl

# Run TypeScript's full 160x96 startup acceptance gate.
ts-startup-gate:
    npm --prefix implementations/typescript run gate:startup

# Run Python's full 160x96 scheduled-control acceptance trajectory.
py-scheduled-gate:
    uv run --project implementations/python python implementations/python/benchmark/scheduled_gate.py

# Run Julia's full 160x96 scheduled-control acceptance trajectory.
jl-scheduled-gate:
    julia --threads=auto --project=implementations/julia implementations/julia/benchmark/scheduled_gate.jl

# Run TypeScript's full 160x96 scheduled-control acceptance trajectory.
ts-scheduled-gate:
    npm --prefix implementations/typescript run gate:scheduled

# Compare TypeScript result artifacts.
ts-compare results="results/typescript":
    npm --prefix implementations/typescript run compare -- "{{ results }}"

# Describe the Python implementation.
py-describe:
    uv run --project implementations/python foilbench-py describe

# Describe the Julia implementation.
jl-describe:
    julia --project=implementations/julia implementations/julia/bin/foilbench-jl describe

# Describe the TypeScript implementation.
ts-describe:
    npm --prefix implementations/typescript run describe

# Run a Python benchmark matrix.
py-bench matrix="benchmark-matrices/smoke.json":
    uv run --project implementations/python foilbench-py bench "{{ matrix }}"

# Run a Julia benchmark matrix.
jl-bench matrix="benchmark-matrices/smoke.json":
    julia --project=implementations/julia implementations/julia/bin/foilbench-jl bench "{{ matrix }}"

# Compare Python result artifacts.
py-compare results="results/python":
    uv run --project implementations/python foilbench-py compare "{{ results }}"

# Compare Julia result artifacts.
jl-compare results="results/julia":
    julia --project=implementations/julia implementations/julia/bin/foilbench-jl compare "{{ results }}"

# Run the Julia 160x96 interactive-throughput acceptance gate.
jl-preview-gate:
    julia --threads=auto --project=implementations/julia implementations/julia/benchmark/preview_gate.jl

# Open the accepted chaotic-wake experiment in the Python viewer.
py-chaos:
    just py-view scenarios/airfoil/chaotic-experimental.json stable-fluids

# Open the accepted chaotic-wake experiment in the Julia viewer.
jl-chaos:
    just jl-view scenarios/airfoil/chaotic-experimental.json stable-fluids

# Run Python's deterministic chaotic-wake parameter sweep.
py-chaos-sweep:
    uv run --project implementations/python python experiments/chaotic_wake_sweep.py

# Run Python's paired chaotic-wake trajectory experiment.
py-chaos-paired:
    uv run --project implementations/python python experiments/chaos_sensitivity.py

# Run Julia's deterministic chaotic-wake parameter sweep.
jl-chaos-sweep:
    julia --project=implementations/julia implementations/julia/experiments/chaotic_wake_sweep.jl

# Run Julia's paired chaotic-wake trajectory experiment.
jl-chaos-paired:
    julia --project=implementations/julia implementations/julia/experiments/chaos_sensitivity.jl

# Run TypeScript's deterministic chaotic-wake parameter sweep.
ts-chaos-sweep:
    npm --prefix implementations/typescript run chaos:sweep

# Run TypeScript's paired chaotic-wake trajectory experiment.
ts-chaos-paired:
    npm --prefix implementations/typescript run chaos:paired

# Run the matched raw-solver drag calibration in Python.
py-drag-calibration:
    uv run --project implementations/python python experiments/drag_calibration.py --output results/drag-calibration-python.json

# Run the matched raw-solver drag calibration in Julia.
jl-drag-calibration:
    julia --project=implementations/julia implementations/julia/experiments/drag_calibration.jl results/drag-calibration-julia.json

# Run the matched raw-solver drag calibration in TypeScript.
ts-drag-calibration:
    npm --prefix implementations/typescript run drag:calibration -- results/drag-calibration-typescript.json

# Run all three language drag calibrations for the policy decision gate.
drag-calibration:
    uv run --project implementations/python python experiments/drag_calibration.py --output results/drag-calibration-python.json
    julia --project=implementations/julia implementations/julia/experiments/drag_calibration.jl results/drag-calibration-julia.json
    npm --prefix implementations/typescript run drag:calibration -- results/drag-calibration-typescript.json
