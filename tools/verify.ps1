[CmdletBinding()]
param(
    [switch]$Python,
    [switch]$Julia
)

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'

if (-not $Python -and -not $Julia) {
    $Python = $true
    $Julia = $true
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
            '-e', 'using FoilBenchJulia, GLMakie, JSON3; include(joinpath(pwd(), "implementations", "julia", "src", "viewer", "glmakie_app.jl")); println("Julia viewer environment loaded")'
        )
    }
}
finally {
    Pop-Location
}
