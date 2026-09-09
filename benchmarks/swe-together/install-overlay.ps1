param(
  [Parameter(Mandatory = $true)][string]$SWEPath
)

$ErrorActionPreference = 'Stop'
$targetRoot = (Resolve-Path $SWEPath).Path
if (-not (Test-Path -LiteralPath (Join-Path $targetRoot '.git'))) {
  throw "SWEPath is not a Git checkout: $targetRoot"
}
$expectedCommit = (Get-Content -LiteralPath (Join-Path $PSScriptRoot 'UPSTREAM_COMMIT') -Raw).Trim()
$actualCommit = (& git -C $targetRoot rev-parse HEAD).Trim()
if ($actualCommit -ne $expectedCommit) {
  Write-Warning "Overlay was verified at $expectedCommit; checkout is $actualCommit"
}
$overlayRoot = Join-Path $PSScriptRoot 'overlay'
$files = Get-ChildItem -LiteralPath $overlayRoot -File -Recurse
foreach ($file in $files) {
  $relative = $file.FullName.Substring($overlayRoot.Length).TrimStart('\', '/')
  $destination = Join-Path $targetRoot $relative
  New-Item -ItemType Directory -Force -Path (Split-Path $destination) | Out-Null
  Copy-Item -LiteralPath $file.FullName -Destination $destination -Force
}
Write-Host "Applied $($files.Count) files to $targetRoot"
Write-Host 'Review with: git -C <SWEPath> status --short'
