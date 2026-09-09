param(
  [Parameter(Mandatory = $true)][string]$Plan,
  [Parameter(Mandatory = $true)][string]$Run,
  [ValidateSet('no-skill', 'baseline', 'ours_v3')][string]$Variant = 'ours_v3',
  [switch]$SkipBootstrap
)

$ErrorActionPreference = 'Stop'
$repoRoot = (Resolve-Path (Join-Path $PSScriptRoot '..\..')).Path
$planPath = (Resolve-Path $Plan).Path
$python = (Get-Command python -ErrorAction Stop).Source
if (-not $env:DEEPSEEK_API_KEY) {
  throw 'DEEPSEEK_API_KEY is required.'
}

$planConfig = Get-Content -LiteralPath $planPath -Raw | ConvertFrom-Json
$planConfig.variant = $Variant
$frozenDir = Join-Path $repoRoot 'benchmarks\generated-plans'
New-Item -ItemType Directory -Force -Path $frozenDir | Out-Null
$frozen = Join-Path $frozenDir "$Run.json"
$planConfig | ConvertTo-Json -Depth 20 | Set-Content -LiteralPath $frozen -Encoding utf8

if (-not $SkipBootstrap) {
  & (Join-Path $PSScriptRoot 'bootstrap.ps1') -Plan $frozen
  if ($LASTEXITCODE -ne 0) { throw 'Bootstrap failed.' }
}
$portableNode = Join-Path $repoRoot '.benchmark-tools\node-v22.22.2-win-x64\node.exe'
if (Test-Path -LiteralPath $portableNode) { $env:BENCHMARK_NODE = $portableNode }

& $python (Join-Path $repoRoot 'experiments\longitudinal-benchmark\validate_plan.py') --plan $frozen
if ($LASTEXITCODE -ne 0) { throw 'Plan validation failed.' }
& (Join-Path $repoRoot 'experiments\longitudinal-benchmark\launch.ps1') -Plan $frozen -Run $Run
if ($LASTEXITCODE -ne 0) { throw 'Launch failed.' }

$runDir = Join-Path $repoRoot "experiments\longitudinal-benchmark\runs\$Run"
Write-Host "Live progress: $runDir\progress.json"
Write-Host "Final result:  $runDir\result.json"
