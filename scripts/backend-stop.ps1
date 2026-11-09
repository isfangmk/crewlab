# Stop AI workbench backend
$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent $PSScriptRoot
$Backend = Join-Path $Root "backend"
$PidFile = Join-Path $Backend ".uvicorn.pid"

if (-not (Test-Path $PidFile)) {
  Write-Host "PID file missing; try port 18080"
  $conn = Get-NetTCPConnection -LocalPort 18080 -State Listen -ErrorAction SilentlyContinue | Select-Object -First 1
  if ($null -eq $conn) {
    Write-Host "backend not running"
    exit 0
  }
  Stop-Process -Id $conn.OwningProcess -Force
  Write-Host "stopped by port PID=$($conn.OwningProcess)"
  exit 0
}

$oldPid = (Get-Content $PidFile -Raw).Trim()
if ($oldPid -and (Get-Process -Id $oldPid -ErrorAction SilentlyContinue)) {
  Stop-Process -Id $oldPid -Force
  Write-Host "stopped backend PID=$oldPid"
} else {
  Write-Host "PID=$oldPid not found"
}
Remove-Item $PidFile -Force -ErrorAction SilentlyContinue
