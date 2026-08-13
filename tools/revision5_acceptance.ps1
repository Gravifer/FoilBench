[CmdletBinding()]
param([string]$OutputRoot)

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'

function Invoke-Checked {
    param([Parameter(Mandatory)][string]$Program, [Parameter(Mandatory)][string[]]$Arguments)
    & $Program @Arguments
    if ($LASTEXITCODE -ne 0) {
        throw "Command failed with exit code ${LASTEXITCODE}: $Program $($Arguments -join ' ')"
    }
}

$repositoryRoot = (Resolve-Path (Join-Path $PSScriptRoot '..')).Path
$fixturePath = Join-Path $repositoryRoot 'spec/proposals/revision5/fixtures/fullsize-acceptance-v2.json'
$fixture = Get-Content -LiteralPath $fixturePath -Raw | ConvertFrom-Json
if ($fixture.schema_version -ne 2 -or $fixture.contract_id -ne 'foilbench-phase3-v1' -or $fixture.contract_revision -ne 5) {
    throw 'Revision 5 acceptance fixture identity is invalid'
}
$artifactRoster = @($fixture.artifact_interchange.required_producers | ForEach-Object { [string]$_ })
$nativeRoster = @($fixture.chaotic_extension.required_producers | ForEach-Object { [string]$_ })
$expectedArtifacts = @('python/native', 'julia/native', 'typescript/browser-worker', 'rust/native')
$expectedNative = @('python/native', 'julia/native', 'typescript/node', 'rust/native')
if (($artifactRoster -join ',') -ne ($expectedArtifacts -join ',')) { throw 'Revision 5 artifact roster drifted' }
if (($nativeRoster -join ',') -ne ($expectedNative -join ',')) { throw 'Revision 5 native roster drifted' }
$artifactRosterText = $artifactRoster -join ','
$nativeRosterText = $nativeRoster -join ','
$timestamp = Get-Date -Format 'yyyyMMdd-HHmmss'
if ([string]::IsNullOrWhiteSpace($OutputRoot)) {
    $OutputRoot = Join-Path $repositoryRoot "results/revision5-acceptance/$timestamp"
}
elseif (-not [System.IO.Path]::IsPathRooted($OutputRoot)) {
    $OutputRoot = Join-Path $repositoryRoot $OutputRoot
}
$OutputRoot = [System.IO.Path]::GetFullPath($OutputRoot)
New-Item -ItemType Directory -Path $OutputRoot -Force | Out-Null
$tsx = Join-Path $repositoryRoot 'implementations/typescript/node_modules/tsx/dist/cli.mjs'
$matrix = Join-Path $repositoryRoot ([string]$fixture.artifact_interchange.matrix)
$benchmarkRoot = Join-Path $OutputRoot 'benchmarks'
$pythonResults = Join-Path $benchmarkRoot 'python'
$juliaResults = Join-Path $benchmarkRoot 'julia'
$typescriptResults = Join-Path $benchmarkRoot 'typescript'
$rustResults = Join-Path $benchmarkRoot 'rust'

Push-Location $repositoryRoot
try {
    Write-Host '==> Revision 5: schema and proposal fixtures'
    Invoke-Checked uv @('run', '--project', 'implementations/python', 'python', 'tools/validate_spec.py')
    Invoke-Checked uv @('run', '--project', 'implementations/python', 'python', 'tools/validate_acceptance_fixtures.py')

    Write-Host '==> Revision 5: independently emitted benchmark artifacts'
    Invoke-Checked uv @('run', '--project', 'implementations/python', 'foilbench-py', 'bench', $matrix, '--output', $pythonResults)
    Invoke-Checked julia @('--threads=auto', '--startup-file=no', '--history-file=no', '--project=implementations/julia', 'implementations/julia/bin/foilbench-jl', 'bench', $matrix, '--output', $juliaResults)
    Invoke-Checked node @($tsx, 'implementations/typescript/src/cli.ts', 'bench', $matrix, $typescriptResults)
    Invoke-Checked cargo @('run', '--release', '--quiet', '--locked', '--manifest-path', 'implementations/rust/Cargo.toml', '-p', 'foilbench-native', '--', 'bench', $matrix, '--output', $rustResults)

    Write-Host '==> Revision 5: every canonical reader and destination solver'
    Invoke-Checked uv @('run', '--project', 'implementations/python', 'python', 'implementations/python/benchmark/interchange_gate.py', $benchmarkRoot)
    Invoke-Checked julia @('--threads=auto', '--startup-file=no', '--history-file=no', '--project=implementations/julia', 'implementations/julia/benchmark/interchange_gate.jl', $benchmarkRoot)
    Invoke-Checked node @($tsx, 'implementations/typescript/src/benchmark/interchangeGate.ts', $benchmarkRoot)
    Invoke-Checked cargo @('run', '--release', '--quiet', '--locked', '--manifest-path', 'implementations/rust/Cargo.toml', '-p', 'foilbench-native', '--', 'interchange', $benchmarkRoot)

    Write-Host '==> Revision 5: every native benchmark comparer'
    Invoke-Checked uv @('run', '--project', 'implementations/python', 'foilbench-py', 'compare', $benchmarkRoot, '--require-complete', '--require-producers', $artifactRosterText)
    Invoke-Checked julia @('--startup-file=no', '--history-file=no', '--project=implementations/julia', 'implementations/julia/bin/foilbench-jl', 'compare', $benchmarkRoot, '--require-complete', '--require-producers', $artifactRosterText)
    Invoke-Checked node @($tsx, 'implementations/typescript/src/cli.ts', 'compare', $benchmarkRoot, '--require-complete', '--require-producers', $artifactRosterText)
    $rustCompare = @('run', '--release', '--quiet', '--locked', '--manifest-path', 'implementations/rust/Cargo.toml', '-p', 'foilbench-native', '--', 'compare', $benchmarkRoot)
    foreach ($producer in $artifactRoster) { $rustCompare += @('--require-producer', $producer) }
    Invoke-Checked cargo $rustCompare

    $casesPath = Join-Path $repositoryRoot ([string]$fixture.chaotic_extension.fixture)
    $cases = Get-Content -LiteralPath $casesPath -Raw | ConvertFrom-Json
    $chaosDirectory = Join-Path $OutputRoot 'chaos'
    New-Item -ItemType Directory -Path $chaosDirectory -Force | Out-Null
    $scenario = Join-Path $repositoryRoot ([string]$cases.scenario)
    $preflight = $cases.initialization_preflight
    $selected = $preflight.case
    $preflights = @(
        (Join-Path $chaosDirectory 'python-preflight.json'),
        (Join-Path $chaosDirectory 'julia-preflight.json'),
        (Join-Path $chaosDirectory 'typescript-preflight.json'),
        (Join-Path $chaosDirectory 'rust-preflight.json')
    )
    Write-Host '==> Revision 5: paired-sensitivity initialization preflight'
    Invoke-Checked uv @('run', '--project', 'implementations/python', 'python', 'experiments/chaos_sensitivity.py', '--scenario', $scenario, '--duration', [string]$preflight.duration, '--epsilon', [string]$preflight.epsilon, '--single', [string]$selected.reynolds, [string]$selected.angle_degrees, [string]$selected.resolution[0], [string]$selected.resolution[1], '--output', $preflights[0])
    Invoke-Checked julia @('--threads=auto', '--startup-file=no', '--history-file=no', '--project=implementations/julia', 'implementations/julia/experiments/chaos_sensitivity.jl', $scenario, $preflights[1], [string]$preflight.duration, [string]$preflight.epsilon, [string]$selected.reynolds, [string]$selected.angle_degrees, [string]$selected.resolution[0], [string]$selected.resolution[1])
    Invoke-Checked node @($tsx, 'implementations/typescript/src/cli.ts', 'chaos-preflight', $scenario, $preflights[2])
    Invoke-Checked cargo @('run', '--release', '--quiet', '--locked', '--manifest-path', 'implementations/rust/Cargo.toml', '-p', 'foilbench-native', '--', 'chaos-preflight', '--output', $preflights[3])
    $preflightValidation = @('run', '--project', 'implementations/python', 'foilbench-py', 'chaos-preflight-validate') + $preflights + @('--require-producers', $nativeRosterText)
    Invoke-Checked uv $preflightValidation

    Write-Host '==> Revision 5: full independently emitted chaotic-wake evidence'
    $chaos = @(
        (Join-Path $chaosDirectory 'python-sweep.json'), (Join-Path $chaosDirectory 'python-sensitivity.json'),
        (Join-Path $chaosDirectory 'julia-sweep.json'), (Join-Path $chaosDirectory 'julia-sensitivity.json'),
        (Join-Path $chaosDirectory 'typescript-sweep.json'), (Join-Path $chaosDirectory 'typescript-sensitivity.json'),
        (Join-Path $chaosDirectory 'rust-sweep.json'), (Join-Path $chaosDirectory 'rust-sensitivity.json')
    )
    Invoke-Checked uv @('run', '--project', 'implementations/python', 'python', 'experiments/chaotic_wake_sweep.py', '--scenario', $scenario, '--duration', [string]$cases.sweep.duration, '--burn-in', [string]$cases.sweep.burn_in, '--output', $chaos[0])
    Invoke-Checked uv @('run', '--project', 'implementations/python', 'python', 'experiments/chaos_sensitivity.py', '--scenario', $scenario, '--duration', [string]$cases.sensitivity.duration, '--epsilon', [string]$cases.sensitivity.epsilon, '--output', $chaos[1])
    Invoke-Checked julia @('--threads=auto', '--startup-file=no', '--history-file=no', '--project=implementations/julia', 'implementations/julia/experiments/chaotic_wake_sweep.jl', $scenario, $chaos[2], [string]$cases.sweep.duration, [string]$cases.sweep.burn_in)
    Invoke-Checked julia @('--threads=auto', '--startup-file=no', '--history-file=no', '--project=implementations/julia', 'implementations/julia/experiments/chaos_sensitivity.jl', $scenario, $chaos[3], [string]$cases.sensitivity.duration, [string]$cases.sensitivity.epsilon)
    Invoke-Checked node @($tsx, 'implementations/typescript/src/cli.ts', 'chaos-sweep', $scenario, $chaos[4])
    Invoke-Checked node @($tsx, 'implementations/typescript/src/cli.ts', 'chaos-paired', $scenario, $chaos[5])
    Invoke-Checked cargo @('run', '--release', '--quiet', '--locked', '--manifest-path', 'implementations/rust/Cargo.toml', '-p', 'foilbench-native', '--', 'chaos-sweep', '--output', $chaos[6])
    Invoke-Checked cargo @('run', '--release', '--quiet', '--locked', '--manifest-path', 'implementations/rust/Cargo.toml', '-p', 'foilbench-native', '--', 'chaos-paired', '--output', $chaos[7])
    $chaosValidation = @('run', '--project', 'implementations/python', 'foilbench-py', 'chaos-validate') + $chaos + @('--require-producers', $nativeRosterText)
    Invoke-Checked uv $chaosValidation

    $commit = (& git rev-parse HEAD).Trim()
    if ($LASTEXITCODE -ne 0) { throw 'Unable to identify acceptance commit' }
    [ordered]@{
        schema_version = 2; contract_id = 'foilbench-phase3-v1'; contract_revision = 5
        status = 'passed'; completed_at = (Get-Date).ToUniversalTime().ToString('o')
        git_commit = $commit; fixture = 'spec/proposals/revision5/fixtures/fullsize-acceptance-v2.json'
        artifact_producers = $artifactRoster; native_producers = $nativeRoster
        benchmark_directories = @($pythonResults, $juliaResults, $typescriptResults, $rustResults)
        chaos_preflight_artifacts = $preflights; chaos_artifacts = $chaos
    } | ConvertTo-Json -Depth 8 | Set-Content -LiteralPath (Join-Path $OutputRoot 'representative-evidence.json') -Encoding utf8NoBOM
    Write-Host "Revision 5 generated acceptance evidence: $OutputRoot"
}
finally { Pop-Location }
