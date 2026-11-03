# Start the local FishMemory HTTP service (127.0.0.1) detached from this shell.
# Contract: docs/16-DSH原生插件集成.md §4-§5/§8. The service must keep running while
# DSH is open; restarting DSH does not start it automatically.
#
# Usage:  powershell -ExecutionPolicy Bypass -File scripts\start-fishmemory.ps1
#         powershell -ExecutionPolicy Bypass -File scripts\start-fishmemory.ps1 -Port 8765

param(
    [int]$Port = 0,
    [string]$Config = ''
)

$ErrorActionPreference = 'Stop'
$root = Split-Path -Parent $PSScriptRoot
$python = Join-Path $root '.venv\Scripts\python.exe'
if (-not (Test-Path $python)) {
    $python = 'python'
}
if ([string]::IsNullOrWhiteSpace($Config)) {
    $Config = Join-Path $root 'fishmemory.local.json'
}
if (-not (Test-Path $Config)) {
    Write-Error "config not found: $Config (run: .venv\Scripts\python.exe -m fishmemory init --config fishmemory.local.json)"
    exit 2
}

# Resolve the configured port from the config when -Port is not given.
$resolvedPort = $Port
if ($resolvedPort -le 0) {
    try {
        $raw = Get-Content -LiteralPath $Config -Raw -Encoding UTF8 | ConvertFrom-Json
        if ($raw.service -and $raw.service.port) { $resolvedPort = [int]$raw.service.port }
    } catch {
        $resolvedPort = 8765
    }
    if ($resolvedPort -le 0) { $resolvedPort = 8765 }
}

$health = "http://127.0.0.1:$resolvedPort/healthz"
try {
    $existing = Invoke-WebRequest -Uri $health -UseBasicParsing -TimeoutSec 2
    if ($existing.StatusCode -eq 200) {
        Write-Output "FishMemory service already running: $($existing.Content)"
        exit 0
    }
} catch {
    # Not running yet: fall through to start it.
}

$logDir = Join-Path $root '.fishmemory'
New-Item -ItemType Directory -Force -Path $logDir | Out-Null
$outLog = Join-Path $logDir 'service.out.log'
$errLog = Join-Path $logDir 'service.err.log'
$serveArgs = @('-m', 'fishmemory', 'serve', '--config', $Config)
if ($Port -gt 0) { $serveArgs += @('--port', "$Port") }
# Start detached from this shell. Redirect to files instead of pipes so the service
# does not depend on the parent process; a later start truncates these two files.
Start-Process -FilePath $python -ArgumentList $serveArgs -WorkingDirectory $root `
    -WindowStyle Hidden -RedirectStandardOutput $outLog -RedirectStandardError $errLog | Out-Null

for ($i = 0; $i -lt 40; $i++) {
    Start-Sleep -Milliseconds 500
    try {
        $response = Invoke-WebRequest -Uri $health -UseBasicParsing -TimeoutSec 2
        if ($response.StatusCode -eq 200) {
            Write-Output "FishMemory service started: $($response.Content)"
            Write-Output "logs: $outLog ; $errLog"
            exit 0
        }
    } catch {
        # keep waiting
    }
}

Write-Error "FishMemory service did not become healthy on $health"
if (Test-Path $errLog) { Write-Output '--- service.err.log (tail) ---'; Get-Content -LiteralPath $errLog -Tail 20 }
if (Test-Path $outLog) { Write-Output '--- service.out.log (tail) ---'; Get-Content -LiteralPath $outLog -Tail 20 }
exit 1

