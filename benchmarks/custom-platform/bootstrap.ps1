param(
  [Parameter(Mandatory = $true)][string]$Plan
)

$ErrorActionPreference = 'Stop'
$repoRoot = (Resolve-Path (Join-Path $PSScriptRoot '..\..')).Path
$planPath = (Resolve-Path -LiteralPath $Plan).Path
$planDir = Split-Path -Parent $planPath
$python = (Get-Command python -ErrorAction Stop).Source
$systemNode = (Get-Command node -ErrorAction SilentlyContinue)
$node = if ($systemNode) { $systemNode.Source } else { $null }
$nodeMajor = if ($node) { [int]((& $node --version).TrimStart('v').Split('.')[0]) } else { 0 }
if ($nodeMajor -ne 22) {
  $toolsDir = Join-Path $repoRoot '.benchmark-tools'
  $nodeVersion = '22.22.2'
  $nodeFolder = Join-Path $toolsDir "node-v$nodeVersion-win-x64"
  $node = Join-Path $nodeFolder 'node.exe'
  if (-not (Test-Path -LiteralPath $node)) {
    New-Item -ItemType Directory -Force -Path $toolsDir | Out-Null
    $archiveName = "node-v$nodeVersion-win-x64.zip"
    $archive = Join-Path $toolsDir $archiveName
    $baseUrl = "https://nodejs.org/dist/v$nodeVersion"
    Invoke-WebRequest "$baseUrl/$archiveName" -OutFile $archive
    $checksums = (Invoke-WebRequest "$baseUrl/SHASUMS256.txt").Content
    $expected = (($checksums -split "`n") | Where-Object { $_ -match "\s+$([regex]::Escape($archiveName))\s*$" } | Select-Object -First 1).Split()[0]
    $actual = (Get-FileHash -LiteralPath $archive -Algorithm SHA256).Hash.ToLowerInvariant()
    if (-not $expected -or $actual -ne $expected.ToLowerInvariant()) {
      throw 'Downloaded Node.js archive failed SHA-256 verification.'
    }
    Expand-Archive -LiteralPath $archive -DestinationPath $toolsDir -Force
    Remove-Item -LiteralPath $archive -Force
  }
}
$env:BENCHMARK_NODE = $node
$npm = Join-Path (Split-Path -Parent $node) 'npm.cmd'
if (-not (Test-Path -LiteralPath $npm)) { $npm = (Get-Command npm -ErrorAction Stop).Source }
& docker info *> $null
if ($LASTEXITCODE -ne 0) { throw 'Docker engine is unavailable.' }

$planConfig = Get-Content -LiteralPath $planPath -Raw | ConvertFrom-Json
$resolvedSuites = @()
foreach ($session in $planConfig.sessions) {
  $suiteRoot = [IO.Path]::GetFullPath((Join-Path $planDir $session.suite_root))
  $suiteConfigPath = Join-Path $suiteRoot 'private\suite.json'
  if (-not (Test-Path -LiteralPath $suiteConfigPath)) {
    throw "Suite is incomplete: $suiteRoot"
  }
  $suiteConfig = Get-Content -LiteralPath $suiteConfigPath -Raw | ConvertFrom-Json
  $sourcePath = [IO.Path]::GetFullPath((Join-Path $suiteRoot $suiteConfig.repository.source_path))
  $repoContainer = [IO.Path]::GetFullPath((Join-Path $repoRoot 'repo'))
  if (-not $sourcePath.StartsWith($repoContainer + [IO.Path]::DirectorySeparatorChar, [StringComparison]::OrdinalIgnoreCase)) {
    throw "Repository target escapes the clone-local repo directory: $sourcePath"
  }
  if (-not (Test-Path -LiteralPath (Join-Path $sourcePath '.git'))) {
    New-Item -ItemType Directory -Force -Path $sourcePath | Out-Null
    & git -C $sourcePath init
    & git -C $sourcePath remote add origin $suiteConfig.repository.url
  }
  $commit = $suiteConfig.repository.base_commit
  & git -C $sourcePath fetch --depth 1 origin $commit
  if ($LASTEXITCODE -ne 0) { throw "Failed to fetch $($suiteConfig.repository.url) at $commit" }
  & git -C $sourcePath checkout --detach --force $commit
  if ($LASTEXITCODE -ne 0) { throw "Failed to checkout $commit" }
  $resolvedSuites += [pscustomobject]@{ Root = $suiteRoot; Config = $suiteConfig }
}

foreach ($packageDir in @('MemoryCore', 'MemoryProxy')) {
  Push-Location (Join-Path $repoRoot $packageDir)
  try {
    if (Test-Path -LiteralPath 'package-lock.json') {
      & $npm ci --ignore-scripts
    } else {
      & $npm install --ignore-scripts --no-audit --no-fund
    }
    if ($LASTEXITCODE -ne 0) { throw "npm dependency installation failed in $packageDir" }
  } finally { Pop-Location }
}

$proxyDockerfile = Join-Path $repoRoot 'experiments\click-benchmark\pilot\Dockerfile.proxy'
& docker build -f $proxyDockerfile -t click-pilot-proxy:local $repoRoot
if ($LASTEXITCODE -ne 0) { throw 'Failed to build the observation proxy image.' }

foreach ($item in $resolvedSuites) {
  $suiteRoot = $item.Root
  $images = $item.Config.images
  & docker build -f (Join-Path $suiteRoot 'Dockerfile') -t $images.grader $suiteRoot
  if ($LASTEXITCODE -ne 0) { throw "Failed to build $($images.grader)" }
  & docker build --build-arg "EVALUATOR_IMAGE=$($images.grader)" -f (Join-Path $suiteRoot 'Dockerfile.agent') -t $images.agent $suiteRoot
  if ($LASTEXITCODE -ne 0) { throw "Failed to build $($images.agent)" }
  & docker build --build-arg "AGENT_IMAGE=$($images.agent)" -f (Join-Path $repoRoot 'experiments\click-benchmark\pilot\Dockerfile.claude') -t $images.claude_cli (Join-Path $repoRoot 'experiments\click-benchmark\pilot')
  if ($LASTEXITCODE -ne 0) { throw "Failed to build $($images.claude_cli)" }

  $env:BENCHMARK_SUITE_ROOT = $suiteRoot
  & $python (Join-Path $repoRoot 'experiments\click-benchmark\bench.py') lock-environment
  if ($LASTEXITCODE -ne 0) { throw "Failed to lock grader environment for $suiteRoot" }
  & $python (Join-Path $repoRoot 'experiments\click-benchmark\bench.py') lock-agent-environment
  if ($LASTEXITCODE -ne 0) { throw "Failed to lock agent environment for $suiteRoot" }
  & $python (Join-Path $repoRoot 'experiments\click-benchmark\validate_import.py') --suite $suiteRoot
  if ($LASTEXITCODE -ne 0) { throw "Suite validation failed: $suiteRoot" }
}

& $python (Join-Path $repoRoot 'experiments\longitudinal-benchmark\validate_plan.py') --plan $planPath --check-images
if ($LASTEXITCODE -ne 0) { throw 'Image-aware plan validation failed.' }
Write-Host 'Bootstrap and preflight completed.'
