# Stop the local FishMemory HTTP service started by scripts\start-fishmemory.ps1.
# Only processes whose command line contains "fishmemory serve" are stopped; the MCP
# stdio server (`fishmemory.mcp_server`) is left untouched.
#
# Usage:  powershell -ExecutionPolicy Bypass -File scripts\stop-fishmemory.ps1

$ErrorActionPreference = 'Stop'
$targets = Get-CimInstance Win32_Process |
    Where-Object { $_.Name -match '^python' -and $_.CommandLine -match 'fishmemory[\\/ ]?serve' }

if (-not $targets) {
    Write-Output 'FishMemory service is not running.'
    exit 0
}

foreach ($target in $targets) {
    Write-Output "Stopping pid=$($target.ProcessId): $($target.CommandLine)"
    try { Stop-Process -Id $target.ProcessId -Force -ErrorAction Stop } catch { Write-Warning "stop pid $($target.ProcessId) failed: $($_.Exception.Message)" }
}

Start-Sleep -Milliseconds 800
$health = 'http://127.0.0.1:8765/healthz'
try {
    Invoke-WebRequest -Uri $health -UseBasicParsing -TimeoutSec 2 | Out-Null
    Write-Warning "port 8765 still answers /healthz; another service process may be running."
} catch {
    Write-Output 'FishMemory service stopped.'
}

