# FoilBench development shortcuts. Run `just` to list them.
set windows-shell := ["pwsh", "-NoLogo", "-NoProfile", "-Command"]

# List the available shortcuts.
default:
    @just --list

# Install every currently implemented language environment.
setup: py-setup jl-setup ts-setup

# Install the Python reference and development dependencies.
py-setup:
    uv sync --project implementations/python --all-groups

# Instantiate both the Julia solver and GLMakie viewer environments.
jl-setup:
    julia --project=implementations/julia -e 'using Pkg; Pkg.instantiate()'
    julia --project=implementations/julia/viewer -e 'using Pkg; Pkg.instantiate()'

# Install the TypeScript implementation.
ts-setup:
    npm --prefix implementations/typescript ci
    npm --prefix implementations/typescript run setup:browser

# Run all Python and Julia checks.
verify:
    pwsh -NoProfile -File tools/verify.ps1

# Run only the Python checks.
verify-python:
    pwsh -NoProfile -File tools/verify.ps1 -Python

# Run only the Julia checks.
verify-julia:
    pwsh -NoProfile -File tools/verify.ps1 -Julia

# Run only the TypeScript checks.
verify-typescript:
    pwsh -NoProfile -File tools/verify.ps1 -TypeScript

# Open the Python viewer.
py-view scenario="scenarios/airfoil/default.json" solver="stable-fluids":
    uv run --project implementations/python foilbench-py view "{{ scenario }}" --solver "{{ solver }}"

# Open the Julia viewer.
jl-view scenario="scenarios/airfoil/default.json" solver="stable-fluids":
    julia --threads=auto --project=implementations/julia/viewer implementations/julia/bin/foilbench-jl view "{{ scenario }}" --solver "{{ solver }}"

# Open the TypeScript development viewer.
ts-view scenario="scenarios/airfoil/default.json" solver="stable-fluids":
    npm --prefix implementations/typescript run view -- "{{ scenario }}" --solver "{{ solver }}"

# Run a TypeScript Chromium benchmark matrix.
ts-bench matrix="benchmark-matrices/smoke.json":
    npm --prefix implementations/typescript run bench -- "{{ matrix }}"

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
    julia --project=implementations/julia implementations/julia/experiments/paired_trajectory.jl
