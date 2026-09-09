param(
  [Parameter(Mandatory=$true)][string]$Plan,
  [Parameter(Mandatory=$true)][string]$Run,
  [Parameter(Mandatory=$true)][string]$ResumeFrom,
  [Parameter(Mandatory=$true)][string]$Suite,
  [string]$LangfuseConfig = 'D:/cc-proxy-smoke/langfuse-20260904/langfuse.private.json',
  [int]$Timeout = 900
)
$ErrorActionPreference = 'Stop'
$root = $PSScriptRoot
$projectRoot = (Resolve-Path -LiteralPath (Join-Path $root '..\..')).Path
$python = (Get-Command python -ErrorAction Stop).Source
$suitePath = (Resolve-Path -LiteralPath $Suite).Path
$progressPath = Join-Path $root "pipeline-$Run.json"
$validationLog = Join-Path $root "validation-$Run.log"
$pipelineLog = Join-Path $root "pipeline-$Run.log"

function Write-PipelineState([string]$Stage, [string]$Status, [hashtable]$Extra = @{}) {
  $value = [ordered]@{
    schema_version = 1
    run = $Run
    resume_from = $ResumeFrom
    stage = $Stage
    status = $Status
    updated_at = (Get-Date).ToString('o')
    validation_report = (Join-Path $suitePath 'reports\validation.json')
    validation_log = $validationLog
    experiment_progress = (Join-Path $root "runs\$Run\progress.json")
    experiment_result = (Join-Path $root "runs\$Run\result.json")
  }
  foreach ($key in $Extra.Keys) { $value[$key] = $Extra[$key] }
  $temporary = "$progressPath.$PID.tmp"
  $value | ConvertTo-Json -Depth 8 | Set-Content -LiteralPath $temporary -Encoding utf8
  Move-Item -LiteralPath $temporary -Destination $progressPath -Force
}

try {
  Write-PipelineState 'VALIDATING_DATASET' 'RUNNING'
  $env:BENCHMARK_SUITE_ROOT = $suitePath
  & $python (Join-Path $projectRoot 'experiments\click-benchmark\validate.py') *>> $validationLog
  if ($LASTEXITCODE -ne 0) { throw "Dataset validation failed with exit code $LASTEXITCODE" }

  Write-PipelineState 'STARTING_EXPERIMENT' 'RUNNING'
  & (Join-Path $root 'launch.ps1') -Plan $Plan -Run $Run -ResumeFrom $ResumeFrom -LangfuseConfig $LangfuseConfig -Timeout $Timeout *>> $pipelineLog
  if ($LASTEXITCODE -ne 0) { throw "Experiment launch failed with exit code $LASTEXITCODE" }

  $resultPath = Join-Path $root "runs\$Run\result.json"
  $experimentProgress = Join-Path $root "runs\$Run\progress.json"
  while (-not (Test-Path -LiteralPath $resultPath)) {
    $phase = $null
    if (Test-Path -LiteralPath $experimentProgress) {
      try { $phase = (Get-Content -LiteralPath $experimentProgress -Raw | ConvertFrom-Json).progress.phase } catch {}
    }
    Write-PipelineState 'EXPERIMENT_RUNNING' 'RUNNING' @{ experiment_phase = $phase }
    Start-Sleep -Seconds 10
  }
  $result = Get-Content -LiteralPath $resultPath -Raw | ConvertFrom-Json
  Write-PipelineState 'FINISHED' $result.status @{ experiment_status = $result.status }
} catch {
  $_ | Out-String | Add-Content -LiteralPath $pipelineLog
  Write-PipelineState 'FAILED' 'FAILED' @{ error = $_.Exception.Message }
  exit 1
}
