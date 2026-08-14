param(
    [string]$OutputRoot
)

$ErrorActionPreference = 'Stop'
$Repository = Split-Path -Parent $PSScriptRoot
$Commit = (& git -C $Repository rev-parse HEAD).Trim()
if ($LASTEXITCODE -ne 0) { throw 'Unable to resolve the tested commit.' }
$TrackedChanges = @(& git -C $Repository status --porcelain --untracked-files=no)
if ($LASTEXITCODE -ne 0) { throw 'Unable to inspect the working tree.' }
if ($TrackedChanges.Count -ne 0) {
    throw 'Scheduled fidelity requires a clean tracked working tree.'
}
if ([string]::IsNullOrWhiteSpace($OutputRoot)) {
    $OutputRoot = Join-Path $Repository "results/revision5-quality/$Commit/scheduled-fidelity"
}
$OutputRoot = [System.IO.Path]::GetFullPath($OutputRoot)
if (Test-Path -LiteralPath $OutputRoot) {
    throw "Scheduled-fidelity output already exists; refusing to rerun: $OutputRoot"
}
New-Item -ItemType Directory -Path $OutputRoot | Out-Null
$Matrix = 'benchmark-matrices/fidelity-recovery.json'

function Invoke-Checked {
    param([scriptblock]$Command, [string]$Label)
    & $Command
    if ($LASTEXITCODE -ne 0) { throw "$Label failed with exit code $LASTEXITCODE" }
}

Push-Location $Repository
try {
    Invoke-Checked { uv run --project implementations/python foilbench-py bench $Matrix --output (Join-Path $OutputRoot 'python') } 'Python scheduled fidelity'
    Invoke-Checked { julia --project=implementations/julia implementations/julia/bin/foilbench-jl bench $Matrix --output (Join-Path $OutputRoot 'julia') } 'Julia scheduled fidelity'
    Invoke-Checked { npm --prefix implementations/typescript run bench -- $Matrix (Join-Path $OutputRoot 'typescript') } 'TypeScript scheduled fidelity'
    Invoke-Checked { cargo run --quiet --manifest-path implementations/rust/Cargo.toml --locked -p foilbench-native -- bench $Matrix --output (Join-Path $OutputRoot 'rust') } 'Rust scheduled fidelity'
    Invoke-Checked { uv run --project implementations/python python tools/validate_scheduled_fidelity.py $OutputRoot --commit $Commit --summary (Join-Path $OutputRoot 'summary.json') } 'Scheduled-fidelity roster validation'
} finally {
    Pop-Location
}

Write-Output $OutputRoot
