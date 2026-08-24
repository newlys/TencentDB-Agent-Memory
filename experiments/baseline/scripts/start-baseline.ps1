[CmdletBinding()]
param(
    [switch]$Restart,
    [ValidateSet('static', 'adaptive_v1')]
    [string]$Profile = 'static',
    [ValidateSet('legacy', 'sop_v1')]
    [string]$TriggerProfile = 'legacy',
    [ValidateSet('legacy', 'precision_v1')]
    [string]$ValueGateProfile = 'legacy',
    [ValidateSet('legacy_v2', 'precision_v3', 'balanced_v4')]
    [string]$ReviewPromptProfile = 'legacy_v2'
)

$ErrorActionPreference = 'Stop'
$repoRoot = (Resolve-Path (Join-Path $PSScriptRoot '..\..\..')).Path
$baselineRoot = Join-Path $repoRoot 'experiments\baseline'
$runtimeRoot = Join-Path $baselineRoot 'runtime'
$coreRoot = Join-Path $repoRoot 'MemoryCore'
$proxyRoot = Join-Path $repoRoot 'MemoryProxy'
$coreTemplate = Join-Path $baselineRoot 'config\tdai-gateway.baseline.yaml'
$coreConfig = Join-Path $runtimeRoot 'tdai-gateway.baseline.yaml'
$proxyTemplate = Join-Path $baselineRoot 'config\memory-proxy.baseline.template.yaml'
$proxyConfig = Join-Path $runtimeRoot 'memory-proxy.baseline.yaml'

foreach ($name in @('DASHSCOPE_API_KEY', 'AFAC_QWEN_BASE_URL')) {
    $value = [Environment]::GetEnvironmentVariable($name)
    if ([string]::IsNullOrWhiteSpace($value)) {
        throw "Required environment variable $name is not set."
    }
}

New-Item -ItemType Directory -Force -Path $runtimeRoot | Out-Null
New-Item -ItemType Directory -Force -Path (Join-Path $runtimeRoot 'proxy-logs') | Out-Null

function ConvertTo-YamlQuoted([string]$Value) {
    return ($Value | ConvertTo-Json -Compress)
}

$upstreamBase = $env:AFAC_QWEN_BASE_URL.TrimEnd('/')
$coreRendered = (Get-Content -LiteralPath $coreTemplate -Raw).Replace('profile: static', "profile: $Profile")
$coreRendered = $coreRendered.Replace('profile: legacy # extraction-trigger', "profile: $TriggerProfile # extraction-trigger")
$coreRendered = $coreRendered.Replace('profile: legacy # extraction-value-gate', "profile: $ValueGateProfile # extraction-value-gate")
$coreRendered = $coreRendered.Replace('reviewPromptProfile: legacy_v2', "reviewPromptProfile: $ReviewPromptProfile")
Set-Content -LiteralPath $coreConfig -Value $coreRendered -Encoding utf8NoBOM
$rendered = (Get-Content -LiteralPath $proxyTemplate -Raw)
$rendered = $rendered.Replace('__UPSTREAM_BASE_URL__', (ConvertTo-YamlQuoted 'http://127.0.0.1:8431'))
$rendered = $rendered.Replace('__UPSTREAM_API_KEY__', (ConvertTo-YamlQuoted $env:DASHSCOPE_API_KEY))
$rendered = $rendered.Replace('__BASELINE_RUNTIME_DIR__', ($runtimeRoot.Replace('\', '/')))
$rendered = $rendered.Replace('__ROUTING_PROFILE__', $Profile)
Set-Content -LiteralPath $proxyConfig -Value $rendered -Encoding utf8NoBOM

function Get-ListeningProcessId([int]$Port) {
    $connection = Get-NetTCPConnection -State Listen -LocalPort $Port -ErrorAction SilentlyContinue |
        Select-Object -First 1
    if ($connection) { return [int]$connection.OwningProcess }
    return $null
}

if ($Restart) {
    foreach ($port in @(8420, 8096, 8430, 8431)) {
        $ownerPid = Get-ListeningProcessId $port
        if ($ownerPid) {
            Stop-Process -Id $ownerPid -Force
            Start-Sleep -Milliseconds 500
        }
    }
}

$workBuddyNode = Join-Path $env:USERPROFILE '.workbuddy\binaries\node\versions\22.22.2\node.exe'
$node = if (Test-Path -LiteralPath $workBuddyNode) {
    $workBuddyNode
} else {
    (Get-Command node -ErrorAction Stop).Source
}
$nodeMajor = (& $node -p 'process.versions.node.split(".")[0]').Trim()
if ($nodeMajor -ne '22') {
    throw "The baseline requires Node.js 22, but $node reports $(& $node --version)."
}
$env:TDAI_GATEWAY_CONFIG = $coreConfig
# Keep the loopback-only Core gateway open so MemoryProxy can call
# /v3/meta/auth/verify (that client intentionally sends no service Bearer).
# Tenant requests are still validated by their sk-mem user key at the Proxy.
$env:TDAI_GATEWAY_API_KEY = ''
$env:TDAI_LLM_API_KEY = $env:DASHSCOPE_API_KEY
$env:TDAI_LLM_BASE_URL = 'http://127.0.0.1:8431'
$env:TDAI_LLM_MODEL = 'qwen3.7-plus'
$env:TDAI_SKILL_RERANK_BASE_URL = $upstreamBase
$env:TDAI_SKILL_RERANK_API_KEY = $env:DASHSCOPE_API_KEY
$env:TDAI_DATA_DIR = Join-Path $env:LOCALAPPDATA 'TencentDB-Agent-Memory\memory-tdai'

$knowledgePid = Get-ListeningProcessId 8430
if (-not $knowledgePid) {
    $env:BASELINE_KNOWLEDGE_PORT = '8430'
    $env:BASELINE_KNOWLEDGE_LOG = Join-Path $runtimeRoot 'knowledge-calls.jsonl'
    $knowledge = Start-Process -FilePath $node -WorkingDirectory $repoRoot -WindowStyle Hidden -PassThru `
        -ArgumentList @((Join-Path $baselineRoot 'scripts\knowledge-mock.mjs')) `
        -RedirectStandardOutput (Join-Path $runtimeRoot 'knowledge.stdout.log') `
        -RedirectStandardError (Join-Path $runtimeRoot 'knowledge.stderr.log')
    $knowledgePid = $knowledge.Id
}

$capturePid = Get-ListeningProcessId 8431
if (-not $capturePid) {
    $env:BASELINE_CAPTURE_PORT = '8431'
    $env:BASELINE_CAPTURE_UPSTREAM = $upstreamBase
    $env:BASELINE_CAPTURE_DIR = Join-Path $baselineRoot 'results\raw\llm-capture'
    $capture = Start-Process -FilePath $node -WorkingDirectory $repoRoot -WindowStyle Hidden -PassThru `
        -ArgumentList @((Join-Path $baselineRoot 'scripts\llm-capture-proxy.mjs')) `
        -RedirectStandardOutput (Join-Path $runtimeRoot 'capture.stdout.log') `
        -RedirectStandardError (Join-Path $runtimeRoot 'capture.stderr.log')
    $capturePid = $capture.Id
}

$corePid = Get-ListeningProcessId 8420
if (-not $corePid) {
    $core = Start-Process -FilePath $node -WorkingDirectory $coreRoot -WindowStyle Hidden -PassThru `
        -ArgumentList @('--import', 'tsx/esm', 'src/gateway/server.ts') `
        -RedirectStandardOutput (Join-Path $runtimeRoot 'core.stdout.log') `
        -RedirectStandardError (Join-Path $runtimeRoot 'core.stderr.log')
    $corePid = $core.Id
}

$deadline = (Get-Date).AddSeconds(45)
do {
    try {
        $health = Invoke-RestMethod -Uri 'http://127.0.0.1:8420/health' -TimeoutSec 2
        if ($health.status -eq 'ok') { break }
    } catch { }
    Start-Sleep -Milliseconds 500
} while ((Get-Date) -lt $deadline)
if (-not $health -or $health.status -ne 'ok') { throw 'MemoryCore did not become healthy.' }

$proxyPid = Get-ListeningProcessId 8096
if (-not $proxyPid) {
    $proxy = Start-Process -FilePath $node -WorkingDirectory $proxyRoot -WindowStyle Hidden -PassThru `
        -ArgumentList @('--import', 'tsx/esm', 'src/index.ts', '--config', $proxyConfig) `
        -RedirectStandardOutput (Join-Path $runtimeRoot 'proxy.stdout.log') `
        -RedirectStandardError (Join-Path $runtimeRoot 'proxy.stderr.log')
    $proxyPid = $proxy.Id
}

$deadline = (Get-Date).AddSeconds(45)
$proxyHealth = $null
do {
    try {
        $proxyHealth = Invoke-RestMethod -Uri 'http://127.0.0.1:8096/health' -TimeoutSec 2
        if ($proxyHealth.status -eq 'ok') { break }
    } catch { }
    Start-Sleep -Milliseconds 500
} while ((Get-Date) -lt $deadline)
if (-not $proxyHealth -or $proxyHealth.status -ne 'ok') { throw 'MemoryProxy did not become healthy.' }

$state = [ordered]@{
    started_at = (Get-Date).ToString('o')
    model_alias = 'glm-5.1'
    upstream_model = 'qwen3.7-plus'
    routing_profile = $Profile
    extraction_trigger_profile = $TriggerProfile
    extraction_value_gate_profile = $ValueGateProfile
    extraction_review_prompt_profile = $ReviewPromptProfile
    core_pid = $corePid
    proxy_pid = $proxyPid
    knowledge_pid = $knowledgePid
    capture_pid = $capturePid
    core_url = 'http://127.0.0.1:8420'
    proxy_url = 'http://127.0.0.1:8096/workbuddy/default'
    knowledge_url = 'http://127.0.0.1:8430'
    capture_url = 'http://127.0.0.1:8431'
    workbuddy_team_id = 'team-dyf7fb74wi'
    workbuddy_agent_id = 'agt-dyf7zr5fjh'
    baseline_task_id = 'task-dz57lia4lg'
    user_id = 'usr-dw4keqm922'
}
$state | ConvertTo-Json | Set-Content -LiteralPath (Join-Path $runtimeRoot 'state.json') -Encoding utf8NoBOM
$state | ConvertTo-Json
