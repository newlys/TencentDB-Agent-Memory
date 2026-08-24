[CmdletBinding()]
param(
    [int]$TargetTotal = 100,
    [string]$SourceRoot = '',
    [string]$CoreUrl = 'http://127.0.0.1:8420'
)

$ErrorActionPreference = 'Stop'
$repoRoot = (Resolve-Path (Join-Path $PSScriptRoot '..\..\..')).Path
$baselineRoot = Join-Path $repoRoot 'experiments\baseline'
if ([string]::IsNullOrWhiteSpace($SourceRoot)) {
    $SourceRoot = Join-Path $baselineRoot 'corpus\sources\claude-skills'
}
$SourceRoot = (Resolve-Path $SourceRoot).Path
$licensePath = Join-Path $SourceRoot 'LICENSE'
$licenseText = Get-Content -LiteralPath $licensePath -Raw
if ($licenseText -notmatch '^MIT License') {
    throw "Source repository is not covered by the expected MIT license: $licensePath"
}

$sourceCommit = (git -C $SourceRoot rev-parse HEAD).Trim()
if ($LASTEXITCODE -ne 0 -or $sourceCommit -notmatch '^[0-9a-f]{40}$') {
    throw 'Could not resolve the source commit.'
}

$headers = @{
    Authorization = 'Bearer local-dev-memory-key'
    'x-tdai-service-id' = 'default'
}
$scope = [ordered]@{
    team_id = 'team-dyf7fb74wi'
    agent_id = 'agt-dyf7zr5fjh'
    user_id = 'usr-dw4keqm922'
    task_id = 'default'
}

$listBody = @{team_id=$scope.team_id; agent_id=$scope.agent_id; limit=1000; offset=0} | ConvertTo-Json
$existingResponse = Invoke-RestMethod -Uri "$CoreUrl/v3/skill/list" -Method Post -Headers $headers -ContentType 'application/json' -Body $listBody
if ($existingResponse.code -ne 0) { throw "Could not list existing skills: $($existingResponse.message)" }
$existing = @($existingResponse.data.items)
$existingByName = @{}
foreach ($skill in $existing) { $existingByName[$skill.name.ToLowerInvariant()] = $skill }

$needed = $TargetTotal - $existing.Count
if ($needed -le 0) {
    Write-Output "Target already met ($($existing.Count) skills)."
    exit 0
}

# Avoid importing skills that probe credentials, call external SaaS, or teach
# offensive/destructive workflows. Only SKILL.md is imported; scripts, assets,
# and other executable resources are intentionally excluded.
$blockedName = '(?i)(red-team|pen-test|threat-detection|incident-response|secrets?-vault|env-secrets|ms365|google-workspace|stripe|email|youtube|scrap|patent|grants|cross-eval|roast|finance|cloud-security|ai-security)'
$blockedContent = @(
    '(?i)ignore (all|any|the|your)?\s*(previous|prior) instructions',
    '(?i)(upload|post|send|exfiltrate).{0,80}(secret|credential|environment variable)',
    '(?i)reverse shell',
    '(?i)curl.{0,120}\|\s*(sh|bash)'
)

function Get-Priority([string]$RelativePath) {
    $p = $RelativePath.Replace('\', '/')
    if ($p -match '^engineering/skills/[^/]+/SKILL\.md$') { return 0 }
    if ($p -match '^engineering-team/skills/[^/]+/SKILL\.md$') { return 1 }
    if ($p -match '^product-team/skills/[^/]+/SKILL\.md$') { return 2 }
    if ($p -match '^research-ops/skills/[^/]+/SKILL\.md$') { return 3 }
    if ($p -match '^productivity/.+/skills/[^/]+/SKILL\.md$') { return 4 }
    if ($p -match '^engineering/.+/skills/[^/]+/SKILL\.md$') { return 5 }
    if ($p -match '^engineering-team/.+/skills/[^/]+/SKILL\.md$') { return 6 }
    return 20
}

$candidates = @()
Get-ChildItem -LiteralPath $SourceRoot -Filter 'SKILL.md' -File -Recurse | ForEach-Object {
    $relative = [IO.Path]::GetRelativePath($SourceRoot, $_.FullName).Replace('\', '/')
    $content = Get-Content -LiteralPath $_.FullName -Raw
    $nameMatch = [regex]::Match($content, '(?m)^name:\s*["'']?([^\r\n"'']+)')
    if (-not $nameMatch.Success) { return }
    $name = $nameMatch.Groups[1].Value.Trim()
    if ($name -match $blockedName) { return }
    if ($existingByName.ContainsKey($name.ToLowerInvariant())) { return }
    if ([Text.Encoding]::UTF8.GetByteCount($content) -gt 131072) { return }
    foreach ($pattern in $blockedContent) {
        if ($content -match $pattern) { return }
    }
    $candidates += [pscustomobject]@{
        name = $name
        relative_path = $relative
        full_path = $_.FullName
        content = $content
        priority = Get-Priority $relative
        bytes = [Text.Encoding]::UTF8.GetByteCount($content)
    }
}

$seen = @{}
$selected = @()
foreach ($candidate in ($candidates | Sort-Object priority, relative_path)) {
    $key = $candidate.name.ToLowerInvariant()
    if ($seen.ContainsKey($key)) { continue }
    $seen[$key] = $true
    $selected += $candidate
    if ($selected.Count -eq $needed) { break }
}
if ($selected.Count -lt $needed) {
    throw "Only $($selected.Count) safe, unique candidates were found; $needed are required."
}

$manifestItems = @()
$index = 0
foreach ($candidate in $selected) {
    $index++
    $payload = [ordered]@{}
    foreach ($entry in $scope.GetEnumerator()) { $payload[$entry.Key] = $entry.Value }
    $payload.name = $candidate.name
    $payload.content = $candidate.content
    $payload.metadata = [ordered]@{
        source_repo = 'https://github.com/alirezarezvani/claude-skills'
        source_commit = $sourceCommit
        source_path = $candidate.relative_path
        source_license = 'MIT'
        imported_for = 'optimization-task-2-baseline'
        executable_resources_included = $false
        security_policy = 'text-only; deny-pattern scan v1'
    }
    $json = $payload | ConvertTo-Json -Depth 8 -Compress
    $response = Invoke-RestMethod -Uri "$CoreUrl/v3/skill/create" -Method Post -Headers $headers -ContentType 'application/json' -Body $json
    if ($response.code -ne 0) {
        throw "Import failed for $($candidate.name): code=$($response.code) message=$($response.message)"
    }
    $hash = (Get-FileHash -LiteralPath $candidate.full_path -Algorithm SHA256).Hash.ToLowerInvariant()
    $manifestItems += [ordered]@{
        name = $candidate.name
        skill_id = $response.data.skill_id
        source_path = $candidate.relative_path
        sha256 = $hash
        bytes = $candidate.bytes
    }
    Write-Progress -Activity 'Importing baseline Skill corpus' -Status "$index / $needed" -PercentComplete (($index / $needed) * 100)
}
Write-Progress -Activity 'Importing baseline Skill corpus' -Completed

$finalResponse = Invoke-RestMethod -Uri "$CoreUrl/v3/skill/list" -Method Post -Headers $headers -ContentType 'application/json' -Body $listBody
$manifest = [ordered]@{
    schema_version = 1
    generated_at = (Get-Date).ToString('o')
    target_total = $TargetTotal
    final_total = $finalResponse.data.total
    scope = $scope
    sources = @(
        [ordered]@{
            repo = 'https://github.com/obra/superpowers'
            commit = 'b36e0829c6d0140e93cfef2ca599b1b07d4a7797'
            license = 'MIT'
            preexisting_count = $existing.Count
        },
        [ordered]@{
            repo = 'https://github.com/alirezarezvani/claude-skills'
            commit = $sourceCommit
            license = 'MIT'
            imported_count = $manifestItems.Count
        }
    )
    import_policy = [ordered]@{
        only_skill_md = $true
        executable_resources_included = $false
        max_skill_bytes = 131072
        deny_pattern_scan = 'v1'
    }
    imported = $manifestItems
}
$manifestPath = Join-Path $baselineRoot 'corpus\manifest.json'
$manifest | ConvertTo-Json -Depth 10 | Set-Content -LiteralPath $manifestPath -Encoding utf8NoBOM
$manifest | Select-Object generated_at, target_total, final_total, sources | ConvertTo-Json -Depth 6

