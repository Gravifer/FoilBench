[CmdletBinding()]
param(
    [string]$OutputRoot
)

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'

function Invoke-Checked {
    param(
        [Parameter(Mandatory)][string]$Program,
        [Parameter(Mandatory)][string[]]$Arguments
    )
    & $Program @Arguments
    if ($LASTEXITCODE -ne 0) {
        throw "Command failed with exit code ${LASTEXITCODE}: $Program $($Arguments -join ' ')"
    }
}

$repositoryRoot = (Resolve-Path (Join-Path $PSScriptRoot '..')).Path
$fixturePath = Join-Path $repositoryRoot 'spec/conformance/fullsize-acceptance.json'
$fixture = Get-Content -LiteralPath $fixturePath -Raw | ConvertFrom-Json
if ($fixture.schema_version -ne 1 -or $fixture.contract_id -ne 'foilbench-phase2-v1' -or $fixture.contract_revision -ne 4) {
    throw 'Revision 4 acceptance fixture identity is invalid'
}

$expectedLanguages = @('python', 'julia', 'typescript')
$languages = @($fixture.artifact_interchange.languages | ForEach-Object { [string]$_ })
if (($languages -join ',') -ne ($expectedLanguages -join ',')) {
    throw "Revision 4 producer roster must be exactly $($expectedLanguages -join ',')"
}
$roster = $languages -join ','
$timestamp = Get-Date -Format 'yyyyMMdd-HHmmss'
if ([string]::IsNullOrWhiteSpace($OutputRoot)) {
    $OutputRoot = Join-Path $repositoryRoot "results/revision4-acceptance/$timestamp"
}
elseif (-not [System.IO.Path]::IsPathRooted($OutputRoot)) {
    $OutputRoot = Join-Path $repositoryRoot $OutputRoot
}
$OutputRoot = [System.IO.Path]::GetFullPath($OutputRoot)
New-Item -ItemType Directory -Path $OutputRoot -Force | Out-Null

$matrix = Join-Path $repositoryRoot ([string]$fixture.artifact_interchange.matrix)
$pythonResults = Join-Path $OutputRoot 'benchmarks/python'
$juliaResults = Join-Path $OutputRoot 'benchmarks/julia'
$typescriptResults = Join-Path $OutputRoot 'benchmarks/typescript'

Push-Location $repositoryRoot
try {
    Write-Host '==> Revision 4: independently emitted benchmark artifacts'
    Invoke-Checked uv @(
        'run', '--project', 'implementations/python', 'foilbench-py',
        'bench', $matrix, '--output', $pythonResults
    )
    Invoke-Checked julia @(
        '--threads=auto', '--startup-file=no', '--history-file=no',
        '--project=implementations/julia', 'implementations/julia/bin/foilbench-jl',
        'bench', $matrix, '--output', $juliaResults
    )
    Invoke-Checked npm @(
        '--prefix', 'implementations/typescript', 'run', 'bench', '--',
        $matrix, $typescriptResults
    )

    Write-Host '==> Revision 4: every native benchmark comparer'
    Invoke-Checked uv @(
        'run', '--project', 'implementations/python', 'foilbench-py',
        'compare', $OutputRoot, '--require-complete', '--require-languages', $roster
    )
    if ([bool]$fixture.artifact_interchange.require_every_comparer) {
        Invoke-Checked julia @(
            '--startup-file=no', '--history-file=no', '--project=implementations/julia',
            'implementations/julia/bin/foilbench-jl', 'compare', $OutputRoot,
            '--require-complete', '--require-languages', $roster
        )
        Invoke-Checked npm @(
            '--prefix', 'implementations/typescript', 'run', 'compare', '--',
            $OutputRoot, '--require-complete', '--require-languages', $roster
        )
    }

    $chaosArtifacts = @()
    if ([bool]$fixture.chaotic_extension.participation_required_for_claim) {
        Write-Host '==> Revision 4: three-language chaotic-wake participation'
        $casesPath = Join-Path $repositoryRoot ([string]$fixture.chaotic_extension.fixture)
        $cases = Get-Content -LiteralPath $casesPath -Raw | ConvertFrom-Json
        $chaosDirectory = Join-Path $OutputRoot 'chaos'
        New-Item -ItemType Directory -Path $chaosDirectory -Force | Out-Null
        $scenario = Join-Path $repositoryRoot ([string]$cases.scenario)
        $duration = [string]$cases.sweep.duration
        $burnIn = [string]$cases.sweep.burn_in
        $sensitivityDuration = [string]$cases.sensitivity.duration
        $epsilon = [string]$cases.sensitivity.epsilon
        $chaosArtifacts = @(
            (Join-Path $chaosDirectory 'python-sweep.json'),
            (Join-Path $chaosDirectory 'python-sensitivity.json'),
            (Join-Path $chaosDirectory 'julia-sweep.json'),
            (Join-Path $chaosDirectory 'julia-sensitivity.json'),
            (Join-Path $chaosDirectory 'typescript-sweep.json'),
            (Join-Path $chaosDirectory 'typescript-sensitivity.json')
        )
        Invoke-Checked uv @(
            'run', '--project', 'implementations/python', 'python',
            'experiments/chaotic_wake_sweep.py', '--scenario', $scenario,
            '--duration', $duration, '--burn-in', $burnIn, '--output', $chaosArtifacts[0]
        )
        Invoke-Checked uv @(
            'run', '--project', 'implementations/python', 'python',
            'experiments/chaos_sensitivity.py', '--scenario', $scenario,
            '--duration', $sensitivityDuration, '--epsilon', $epsilon,
            '--output', $chaosArtifacts[1]
        )
        Invoke-Checked julia @(
            '--threads=auto', '--startup-file=no', '--history-file=no',
            '--project=implementations/julia',
            'implementations/julia/experiments/chaotic_wake_sweep.jl',
            $scenario, $chaosArtifacts[2], $duration, $burnIn
        )
        Invoke-Checked julia @(
            '--threads=auto', '--startup-file=no', '--history-file=no',
            '--project=implementations/julia',
            'implementations/julia/experiments/chaos_sensitivity.jl',
            $scenario, $chaosArtifacts[3], $sensitivityDuration, $epsilon
        )
        Invoke-Checked npm @(
            '--prefix', 'implementations/typescript', 'run', 'chaos:sweep', '--',
            $scenario, $chaosArtifacts[4]
        )
        Invoke-Checked npm @(
            '--prefix', 'implementations/typescript', 'run', 'chaos:paired', '--',
            $scenario, $chaosArtifacts[5]
        )
        $chaosValidationArguments = @(
            'run', '--project', 'implementations/python', 'foilbench-py',
            'chaos-validate'
        )
        $chaosValidationArguments += $chaosArtifacts
        $chaosValidationArguments += @('--require-languages', $roster)
        Invoke-Checked uv $chaosValidationArguments
    }

    $commit = (& git rev-parse HEAD).Trim()
    if ($LASTEXITCODE -ne 0) { throw 'Unable to identify acceptance commit' }
    $evidence = [ordered]@{
        schema_version = 1
        contract_id = 'foilbench-phase2-v1'
        contract_revision = 4
        status = 'passed'
        completed_at = (Get-Date).ToUniversalTime().ToString('o')
        git_commit = $commit
        fixture = 'spec/conformance/fullsize-acceptance.json'
        languages = $languages
        benchmark_matrix = [string]$fixture.artifact_interchange.matrix
        benchmark_directories = @($pythonResults, $juliaResults, $typescriptResults)
        chaos_artifacts = $chaosArtifacts
    }
    $evidence | ConvertTo-Json -Depth 6 |
        Set-Content -LiteralPath (Join-Path $OutputRoot 'representative-evidence.json') -Encoding utf8
    Write-Host "Revision 4 generated acceptance evidence: $OutputRoot"
}
finally {
    Pop-Location
}
