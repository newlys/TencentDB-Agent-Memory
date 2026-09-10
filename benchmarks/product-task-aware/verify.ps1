$ErrorActionPreference = "Stop"
$repoRoot = (Resolve-Path (Join-Path $PSScriptRoot "..\..")).Path
$proxyRoot = Join-Path $repoRoot "MemoryProxy"

Push-Location $proxyRoot
try {
    if (-not (Test-Path (Join-Path $proxyRoot "node_modules"))) {
        npm ci --ignore-scripts
    }
    npx vitest run src/injection/injectors/__tests__/task-aware-skill-injector.test.ts
    if ($LASTEXITCODE -ne 0) { throw "Task-aware product-path verification failed." }
    npm run typecheck
    if ($LASTEXITCODE -ne 0) { throw "MemoryProxy typecheck failed." }
} finally {
    Pop-Location
}

Write-Host "PASS: normal Proxy task-aware lifecycle is wired and deterministic." -ForegroundColor Green
