[CmdletBinding()]
param(
    [Parameter(Mandatory)][ValidateSet('python', 'julia', 'typescript')][string]$Language,
    [Parameter(Mandatory)][ValidateSet('startup', 'preview', 'warm-switch', 'scheduled')][string]$Gate
)

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'
$root = (Resolve-Path (Join-Path $PSScriptRoot '..\..')).Path
$commit = (& git -C $root rev-parse HEAD).Trim()
$configuration = @(
    'spec/conformance/fullsize-acceptance.json',
    'spec/conformance/solver-validity.json',
    'spec/contract-version.json',
    'spec/schemas/scenario.schema.json',
    'benchmark-matrices/preview-gate.json',
    'scenarios/airfoil/default.json'
)
$digestInput = foreach ($path in $configuration) {
    $blob = (& git -C $root rev-parse "$commit`:$path").Trim()
    if ($LASTEXITCODE -ne 0) { throw "cannot identify acceptance input $path at $commit" }
    "$path`0$blob"
}
$configurationDigest = [Convert]::ToHexString(
    [Security.Cryptography.SHA256]::HashData(
        [Text.Encoding]::UTF8.GetBytes(($digestInput -join "`n"))
    )
).ToLowerInvariant()

$commands = @{
    'python/startup' = @('uv', 'run', '--project', 'implementations/python', 'python', 'implementations/python/benchmark/startup_gate.py')
    'python/preview' = @('uv', 'run', '--project', 'implementations/python', 'python', 'implementations/python/benchmark/preview_gate.py')
    'python/warm-switch' = @('uv', 'run', '--project', 'implementations/python', 'python', 'implementations/python/benchmark/warm_switch_gate.py')
    'python/scheduled' = @('uv', 'run', '--project', 'implementations/python', 'python', 'implementations/python/benchmark/scheduled_gate.py')
    'julia/startup' = @('julia', '--threads=auto', '--project=implementations/julia', 'implementations/julia/benchmark/startup_gate.jl')
    'julia/preview' = @('julia', '--threads=auto', '--project=implementations/julia', 'implementations/julia/benchmark/preview_gate.jl')
    'julia/warm-switch' = @('julia', '--threads=auto', '--project=implementations/julia', 'implementations/julia/benchmark/warm_switch_gate.jl')
    'julia/scheduled' = @('julia', '--threads=auto', '--project=implementations/julia', 'implementations/julia/benchmark/scheduled_gate.jl')
    'typescript/startup' = @('npm', '--prefix', 'implementations/typescript', 'run', 'gate:startup')
    'typescript/preview' = @('npm', '--prefix', 'implementations/typescript', 'run', 'gate:preview')
    'typescript/warm-switch' = @('npm', '--prefix', 'implementations/typescript', 'run', 'gate:warm-switch')
    'typescript/scheduled' = @('npm', '--prefix', 'implementations/typescript', 'run', 'gate:scheduled')
}
$selected = $commands["$Language/$Gate"]
$directory = Join-Path $root "results/ci-cells/$commit/$Language/$Gate"
New-Item -ItemType Directory -Force -Path $directory | Out-Null
$logPath = Join-Path $directory 'gate.log'
Push-Location $root
try {
    & $selected[0] $selected[1..($selected.Count - 1)] 2>&1 | Tee-Object -LiteralPath $logPath
    if ($LASTEXITCODE -ne 0) { throw "acceptance cell failed with exit code $LASTEXITCODE" }
    $executionTarget = if ($Language -eq 'typescript') {
        if ($Gate -eq 'preview') { 'browser-worker' } else { 'node' }
    } else { 'native' }
    @{
        schema_version = 1
        commit = $commit
        configuration_digest = $configurationDigest
        producer = $Language
        execution_target = $executionTarget
        gate = $Gate
        status = 'passed'
        log_file = 'gate.log'
        log_sha256 = (Get-FileHash -LiteralPath $logPath -Algorithm SHA256).Hash.ToLowerInvariant()
    } | ConvertTo-Json | Set-Content -LiteralPath (Join-Path $directory 'cell.json') -Encoding utf8NoBOM
}
finally { Pop-Location }
