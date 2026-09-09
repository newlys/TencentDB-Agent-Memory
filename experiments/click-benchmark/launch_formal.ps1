param(
  [string]$Stamp = (Get-Date -Format 'yyyyMMdd-HHmmss'),
  [ValidateSet('no-skill','baseline')][string[]]$Variants = @('no-skill','baseline'),
  [string]$Suite = $PSScriptRoot
)
$ErrorActionPreference='Stop'
$engine=$PSScriptRoot
$root=(Resolve-Path -LiteralPath $Suite).Path
$env:BENCHMARK_SUITE_ROOT=$root
$python=(Get-Command python -ErrorAction Stop).Source
$launchPath=Join-Path $root "reports/formal-launch-$Stamp.json"

function Start-Variant([string]$Run,[string]$Variant){
  $stdout=Join-Path $root "runtime/runs/$Run/launcher.stdout.log"
  $stderr=Join-Path $root "runtime/runs/$Run/launcher.stderr.log"
  New-Item -ItemType Directory -Force -Path (Split-Path -Parent $stdout)|Out-Null
  $args=@((Join-Path $engine 'session_driver.py'),'--run',$Run,'--variant',$Variant)
  $process=Start-Process -FilePath $python -ArgumentList $args -WorkingDirectory $engine -WindowStyle Hidden -PassThru -RedirectStandardOutput $stdout -RedirectStandardError $stderr
  return [ordered]@{run_id=$Run;variant=$Variant;pid=$process.Id;command=@('python')+$args;stdout=$stdout;stderr=$stderr;result_json=(Join-Path $root "reports/$Run.json");session_json=(Join-Path $root "runtime/runs/$Run/pilot/session.json")}
}

$suiteConfig=Get-Content -LiteralPath (Join-Path $root 'private/suite.json') -Raw|ConvertFrom-Json
$manifest=[ordered]@{
  schema_version=1
  launched_at=(Get-Date).ToString('o')
  benchmark=$suiteConfig.benchmark_id
  base_commit=$suiteConfig.repository.base_commit
  model=$suiteConfig.agent.model
  protocol=(Join-Path $root 'private/protocol.json')
  status='STARTING'
  variants=@()
}
foreach($variant in $Variants){
  $manifest.variants+=Start-Variant "formal-$variant-$Stamp" $variant
}
$manifest|ConvertTo-Json -Depth 8|Set-Content -LiteralPath $launchPath -Encoding utf8NoBOM

$deadline=(Get-Date).AddSeconds(150)
do {
  $ready=$true
  foreach($variant in $manifest.variants){
    $sessionReady=Test-Path -LiteralPath $variant.session_json
    $started=Test-Path -LiteralPath (Join-Path $root "runtime/runs/$($variant.run_id)/pilot/turn-001/invocation.json")
    $alive=$null -ne (Get-Process -Id $variant.pid -ErrorAction SilentlyContinue)
    if(-not ($sessionReady -and $started -and $alive)){$ready=$false}
  }
  if(-not $ready){Start-Sleep -Seconds 2}
} while(-not $ready -and (Get-Date)-lt $deadline)

$manifest.status=if($ready){'RUNNING'}else{'STARTUP_CHECK_FAILED'}
$manifest.checked_at=(Get-Date).ToString('o')
foreach($variant in $manifest.variants){
  $variant.process_alive=$null -ne (Get-Process -Id $variant.pid -ErrorAction SilentlyContinue)
  $variant.first_task_started=Test-Path -LiteralPath (Join-Path $root "runtime/runs/$($variant.run_id)/pilot/turn-001/invocation.json")
}
$manifest|ConvertTo-Json -Depth 8|Set-Content -LiteralPath $launchPath -Encoding utf8NoBOM
$manifest|ConvertTo-Json -Depth 8|Set-Content -LiteralPath (Join-Path $root 'reports/formal-launch-latest.json') -Encoding utf8NoBOM
$manifest|ConvertTo-Json -Depth 8
if(-not $ready){exit 1}
