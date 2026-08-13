[CmdletBinding()]
param(
    [Parameter(Mandatory)][ValidateSet('python', 'julia', 'typescript', 'rust')][string]$Implementation,
    [Parameter(Mandatory)][ValidateSet('native', 'node', 'browser-worker', 'wasm-browser')][string]$ExecutionTarget,
    [Parameter(Mandatory)][ValidateSet('startup', 'preview', 'warm-switch', 'scheduled', 'production-browser')][string]$Gate
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
    'spec/conformance/fullsize-acceptance-v2.json',
    'spec/schemas/acceptance-cell-v2.schema.json',
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

$key = "$Implementation/$ExecutionTarget/$Gate"
$commands = @{
    'python/native/startup' = @('uv', 'run', '--project', 'implementations/python', 'python', 'implementations/python/benchmark/startup_gate.py')
    'python/native/preview' = @('uv', 'run', '--project', 'implementations/python', 'python', 'implementations/python/benchmark/preview_gate.py')
    'python/native/warm-switch' = @('uv', 'run', '--project', 'implementations/python', 'python', 'implementations/python/benchmark/warm_switch_gate.py')
    'python/native/scheduled' = @('uv', 'run', '--project', 'implementations/python', 'python', 'implementations/python/benchmark/scheduled_gate.py')
    'julia/native/startup' = @('julia', '--threads=auto', '--project=implementations/julia', 'implementations/julia/benchmark/startup_gate.jl')
    'julia/native/preview' = @('julia', '--threads=auto', '--project=implementations/julia', 'implementations/julia/benchmark/preview_gate.jl')
    'julia/native/warm-switch' = @('julia', '--threads=auto', '--project=implementations/julia', 'implementations/julia/benchmark/warm_switch_gate.jl')
    'julia/native/scheduled' = @('julia', '--threads=auto', '--project=implementations/julia', 'implementations/julia/benchmark/scheduled_gate.jl')
    'typescript/node/startup' = @('npm', '--prefix', 'implementations/typescript', 'run', 'gate:startup')
    'typescript/browser-worker/preview' = @('npm', '--prefix', 'implementations/typescript', 'run', 'gate:preview')
    'typescript/node/warm-switch' = @('npm', '--prefix', 'implementations/typescript', 'run', 'gate:warm-switch')
    'typescript/node/scheduled' = @('npm', '--prefix', 'implementations/typescript', 'run', 'gate:scheduled')
    'rust/wasm-browser/preview' = @('npm', '--prefix', 'implementations/typescript', 'run', 'gate:rust-wasm-preview')
    'rust/wasm-browser/production-browser' = @('npm', '--prefix', 'implementations/typescript', 'run', 'gate:rust-wasm-production')
}
$directory = Join-Path $root "results/ci-cells/$commit/$Implementation/$ExecutionTarget/$Gate"
New-Item -ItemType Directory -Force -Path $directory | Out-Null
$logPath = Join-Path $directory 'gate.log'
$evidencePath = Join-Path $directory 'evidence.json'
if ($Implementation -eq 'rust' -and $ExecutionTarget -eq 'native') {
    if ($Gate -eq 'production-browser') { throw "rust/native does not define production-browser" }
    $selected = @(
        'cargo', 'run', '--release', '--quiet', '--locked',
        '--manifest-path', 'implementations/rust/Cargo.toml',
        '-p', 'foilbench-native', '--', 'gate', $Gate, '--output', $evidencePath
    )
}
else {
    $selected = $commands[$key]
    if ($null -eq $selected) { throw "unsupported Revision 5 acceptance cell $key" }
}

$started = [Diagnostics.Stopwatch]::StartNew()
Push-Location $root
try {
    & $selected[0] $selected[1..($selected.Count - 1)] 2>&1 | Tee-Object -LiteralPath $logPath
    if ($LASTEXITCODE -ne 0) { throw "acceptance cell failed with exit code $LASTEXITCODE" }
}
finally {
    $started.Stop()
    Pop-Location
}
if (-not (Test-Path -LiteralPath $evidencePath)) {
    @{
        gate = $Gate
        completed = $true
        wall_seconds = $started.Elapsed.TotalSeconds
    } | ConvertTo-Json | Set-Content -LiteralPath $evidencePath -Encoding utf8NoBOM
}
$thresholds = if ($Gate -eq 'preview') {
    @{ hosted_ci_records_only = $true }
}
else {
    @{ exit_code_zero = $true }
}
$measurements = @{
    wall_seconds = $started.Elapsed.TotalSeconds
    log_bytes = (Get-Item -LiteralPath $logPath).Length
    evidence_sha256 = (Get-FileHash -LiteralPath $evidencePath -Algorithm SHA256).Hash.ToLowerInvariant()
}
@{
    schema_version = 2
    contract_id = 'foilbench-phase3-v1'
    contract_revision = 5
    commit = $commit
    configuration_digest = $configurationDigest
    implementation = $Implementation
    execution_target = $ExecutionTarget
    gate = $Gate
    case = 'default-160x96'
    thresholds = $thresholds
    measurements = $measurements
    status = 'passed'
    log_file = 'gate.log'
    log_sha256 = (Get-FileHash -LiteralPath $logPath -Algorithm SHA256).Hash.ToLowerInvariant()
} | ConvertTo-Json -Depth 8 | Set-Content -LiteralPath (Join-Path $directory 'cell.json') -Encoding utf8NoBOM
