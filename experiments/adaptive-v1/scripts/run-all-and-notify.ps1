param(
  [int]$WaitForPid = 0
)

$ErrorActionPreference = 'Stop'
$repoRoot = (Resolve-Path (Join-Path $PSScriptRoot '..\..\..')).Path
$node = 'C:\Users\cheng\.workbuddy\binaries\node\versions\22.22.2\node.exe'
$logDir = Join-Path $repoRoot 'experiments\adaptive-v1\results'
$logPath = Join-Path $logDir 'all-experiments.log'
$notify = Join-Path $env:USERPROFILE '.codex\skills\smtp-notify\scripts\send_smtp_notification.py'
New-Item -ItemType Directory -Force -Path $logDir | Out-Null

function Invoke-Checked([string]$Label, [scriptblock]$Command) {
  "[$(Get-Date -Format o)] START $Label" | Add-Content -LiteralPath $logPath
  & $Command *>> $logPath
  if ($LASTEXITCODE -ne 0) { throw "$Label failed with exit code $LASTEXITCODE" }
  "[$(Get-Date -Format o)] DONE $Label" | Add-Content -LiteralPath $logPath
}

try {
  Set-Location -LiteralPath $repoRoot
  if ($WaitForPid -gt 0 -and (Get-Process -Id $WaitForPid -ErrorAction SilentlyContinue)) {
    "[$(Get-Date -Format o)] Waiting for existing Task 1 adaptive process $WaitForPid" | Add-Content -LiteralPath $logPath
    Wait-Process -Id $WaitForPid
  }

  $env:EVAL_PROFILE = 'adaptive_v1'
  Invoke-Checked 'Task 2 adaptive WorkBuddy suite' { & $node '.\experiments\baseline\scripts\run-task2-baseline.mjs' }

  Invoke-Checked 'Restart static profile' { & '.\experiments\baseline\scripts\start-baseline.ps1' -Restart -Profile static }
  $env:EVAL_PROFILE = 'static'
  Invoke-Checked 'Task 1 static regression suite' { & $node '.\experiments\baseline\scripts\run-task1-baseline.mjs' }
  Invoke-Checked 'Task 2 static WorkBuddy suite' { & $node '.\experiments\baseline\scripts\run-task2-baseline.mjs' }

  Invoke-Checked 'Restart adaptive profile' { & '.\experiments\baseline\scripts\start-baseline.ps1' -Restart -Profile adaptive_v1 }
  Invoke-Checked 'Proxy adaptive tests' {
    & '.\MemoryProxy\node_modules\.bin\vitest.cmd' run --root '.\MemoryProxy' `
      '.\src\skill\__tests__\adaptive-context.test.ts' `
      '.\src\injection\injectors\__tests__\skill-injector-adaptive.test.ts'
  }
  Invoke-Checked 'Core adaptive tests' {
    & '.\MemoryProxy\node_modules\.bin\vitest.cmd' run --root '.\MemoryCore' `
      --config '..\MemoryProxy\vitest.config.ts' `
      '.\src\core\skill\__tests__\adaptive-routing.test.ts' `
      '.\src\core\skill\__tests__\skill-config-adaptive.test.ts'
  }
  Invoke-Checked 'Build A/B report' { & $node '.\experiments\adaptive-v1\scripts\build-comparison-report.mjs' }

  $body = "TencentDB-Agent-Memory adaptive_v1 experiments completed successfully.`nReport: $logDir\comparison.md`nRaw log: $logPath"
  & python $notify --subject 'TencentDB Agent Memory experiments completed' --body $body *>> $logPath
} catch {
  $message = $_.Exception.Message
  "[$(Get-Date -Format o)] FAILED $message" | Add-Content -LiteralPath $logPath
  $body = "TencentDB-Agent-Memory adaptive_v1 experiments failed.`nError: $message`nLog: $logPath"
  & python $notify --subject 'TencentDB Agent Memory experiments failed' --body $body *>> $logPath
  exit 1
}
