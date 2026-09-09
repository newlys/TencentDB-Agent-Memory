param(
  [ValidateSet('baseline', 'ours_v3')][string]$Variant = 'ours_v3',
  [string]$Run = '',
  [int]$AgentTimeout = 1800,
  [int]$CorePort = 49420,
  [int]$ProxyPort = 49096,
  [int]$AdapterPort = 49097,
  [switch]$Resume
)

$ErrorActionPreference = 'Stop'
if (-not $env:DEEPSEEK_API_KEY) { throw 'DEEPSEEK_API_KEY is required.' }
$repoRoot = (Resolve-Path (Join-Path $PSScriptRoot '..\..')).Path
$sweRoot = Join-Path $repoRoot 'SWE-Together'
$python = Join-Path $sweRoot '.venv\Scripts\python.exe'
if (-not (Test-Path -LiteralPath $python)) {
  throw "SWE-Together virtualenv is missing: $python"
}
if (-not $Run) {
  $stamp = Get-Date -Format 'yyyyMMdd-HHmmss'
  $Run = "swe-together-$Variant-$stamp"
}
$internalVariant = if ($Variant -eq 'baseline') { 'native-baseline' } else { 'ours_v3' }
$args = @(
  (Join-Path $sweRoot 'integration\run_baseline_26.py'),
  '--run', $Run, '--variant', $internalVariant,
  '--core-port', "$CorePort", '--proxy-port', "$ProxyPort",
  '--adapter-port', "$AdapterPort", '--agent-timeout', "$AgentTimeout",
  '--input-token-budget', '8000000', '--min-cache-hit-ratio', '0.60',
  '--cache-check-after-requests', '6'
)
if ($Resume) { $args += '--resume' }
$runRoot = Join-Path $sweRoot "integration\runs\$Run"
New-Item -ItemType Directory -Force -Path (Split-Path $runRoot) | Out-Null
$stdout = "$runRoot-launcher.stdout.log"
$stderr = "$runRoot-launcher.stderr.log"
$process = Start-Process -FilePath $python -ArgumentList $args -WorkingDirectory $repoRoot `
  -RedirectStandardOutput $stdout -RedirectStandardError $stderr -WindowStyle Hidden -PassThru
Write-Host "Started $internalVariant run $Run (PID $($process.Id))"
Write-Host "Live progress: $runRoot\status.json"
Write-Host "Final summary: $runRoot\summary.json"
