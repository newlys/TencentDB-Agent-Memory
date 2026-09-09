param(
  [Parameter(Mandatory=$true)][string]$Plan,
  [string]$Run = ("long-no-skill-" + (Get-Date -Format 'yyyyMMdd-HHmmss')),
  [string]$LangfuseConfig,
  [string]$ResumeFrom,
  [int]$Timeout = 900
)
$ErrorActionPreference='Stop'
$root=$PSScriptRoot
$python=(Get-Command python -ErrorAction Stop).Source
$planPath=(Resolve-Path -LiteralPath $Plan).Path
$preflight=& $python (Join-Path $root 'validate_plan.py') --plan $planPath --check-images
if($LASTEXITCODE -ne 0){throw "Longitudinal preflight failed"}
$runRoot=Join-Path $root "runs/$Run"
if(Test-Path -LiteralPath $runRoot){throw "Run already exists: $runRoot"}
$stdout=Join-Path $root "launch-$Run.stdout.log"
$stderr=Join-Path $root "launch-$Run.stderr.log"
$args=@((Join-Path $root 'longitudinal_driver.py'),'--plan',$planPath,'--run',$Run,'--timeout',$Timeout)
if($LangfuseConfig){
  $langfusePath=(Resolve-Path -LiteralPath $LangfuseConfig).Path
  $args+=@('--langfuse-config',$langfusePath)
}
if($ResumeFrom){$args+=@('--resume-from',$ResumeFrom)}
$process=Start-Process -FilePath $python -ArgumentList $args -WorkingDirectory $root -WindowStyle Hidden -PassThru -RedirectStandardOutput $stdout -RedirectStandardError $stderr
$progress=Join-Path $runRoot 'progress.json'
$deadline=(Get-Date).AddSeconds(30)
while(-not (Test-Path -LiteralPath $progress) -and (Get-Date)-lt $deadline){
  if($null -eq (Get-Process -Id $process.Id -ErrorAction SilentlyContinue)){break}
  Start-Sleep -Milliseconds 500
}
$ready=Test-Path -LiteralPath $progress
$launch=[ordered]@{
  experiment_run_id=$Run
  pid=$process.Id
  status=if($ready){'RUNNING'}else{'STARTUP_CHECK_FAILED'}
  plan=$planPath
  progress_json=$progress
  result_json=(Join-Path $runRoot 'result.json')
  stdout=$stdout
  stderr=$stderr
  launched_at=(Get-Date).ToString('o')
}
$launch|ConvertTo-Json -Depth 5
if(-not $ready){exit 1}
