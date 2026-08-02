[CmdletBinding()]
param(
    [switch]$Python,
    [switch]$Julia,
    [switch]$TypeScript
)

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'

if (-not $Python -and -not $Julia -and -not $TypeScript) {
    $Python = $true
    $Julia = $true
    $TypeScript = $true
}

$repositoryRoot = (Resolve-Path (Join-Path $PSScriptRoot '..')).Path

function Invoke-Checked {
    param(
        [Parameter(Mandatory)]
        [string]$Program,

        [Parameter(Mandatory)]
        [string[]]$Arguments
    )

    & $Program @Arguments
    if ($LASTEXITCODE -ne 0) {
        throw "Command failed with exit code ${LASTEXITCODE}: $Program $($Arguments -join ' ')"
    }
}

Push-Location $repositoryRoot
try {
    if ($Python) {
        Write-Host '==> Python: Ruff'
        Invoke-Checked uv @(
            'run', '--project', 'implementations/python',
            'ruff', 'check', 'implementations/python', 'tools/generate_conformance.py'
        )

        Write-Host '==> Python: strict Pyright'
        Invoke-Checked uv @(
            'run', '--project', 'implementations/python',
            'pyright', 'implementations/python'
        )

        Write-Host '==> Python: pytest'
        Invoke-Checked uv @(
            'run', '--project', 'implementations/python',
            'pytest', '-c', 'implementations/python/pyproject.toml'
        )
    }

    if ($Julia) {
        Write-Host '==> Julia: Pkg.test()'
        Invoke-Checked julia @(
            '--startup-file=no', '--history-file=no',
            '--project=implementations/julia',
            '-e', 'using Pkg; Pkg.test()'
        )

        Write-Host '==> Julia: viewer environment load'
        Invoke-Checked julia @(
            '--startup-file=no', '--history-file=no',
            '--project=implementations/julia/viewer',
            '-e', 'using FoilBenchJulia, GLMakie, JSON3; include(joinpath(pwd(), "implementations", "julia", "src", "viewer", "glmakie_app.jl")); viewer_dispatch_visible() = applicable(FoilBenchGLMakie.run_viewer, "scenario.json"); @assert viewer_dispatch_visible(); figure = Figure(); axis = Axis(figure[1, 1]); FoilBenchGLMakie._reserve_left_drag!(axis); @assert !interactions(axis)[:rectanglezoom][1]; colormap = FoilBenchGLMakie._vorticity_colormap(); @assert colormap[129].alpha == 0; @assert first(colormap).alpha > 0.3; println("Julia viewer environment loaded")'
        )
    }

    if ($TypeScript) {
        Write-Host '==> TypeScript: strict checks'
        Push-Location 'implementations/typescript'
        try {
            Invoke-Checked npm @('run', 'check')
            Write-Host '==> TypeScript: Vitest'
            Invoke-Checked npm @('test')
            Write-Host '==> TypeScript: production build'
            Invoke-Checked npm @('run', 'build')
            Write-Host '==> TypeScript: Chromium viewer smoke test'
            Invoke-Checked npm @('run', 'test:browser')
        }
        finally {
            Pop-Location
        }
    }
}
finally {
    Pop-Location
}
