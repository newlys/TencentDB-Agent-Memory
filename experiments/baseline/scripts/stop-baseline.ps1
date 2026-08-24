$ErrorActionPreference = 'Stop'
$runtimeState = Join-Path $PSScriptRoot '..\runtime\state.json'
if (-not (Test-Path -LiteralPath $runtimeState)) {
    Write-Output 'No baseline runtime state was found.'
    exit 0
}

$state = Get-Content -LiteralPath $runtimeState -Raw | ConvertFrom-Json
foreach ($property in @('proxy_pid', 'core_pid', 'knowledge_pid', 'capture_pid')) {
    $targetPid = [int]$state.$property
    $process = Get-Process -Id $targetPid -ErrorAction SilentlyContinue
    if ($process -and $process.ProcessName -eq 'node') {
        Stop-Process -Id $targetPid -Force
        Write-Output "Stopped $property ($targetPid)."
    }
}
