# Stop AI workbench frontend
$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent $PSScriptRoot
$Logs = Join-Path $Root "logs"
$PidFile = Join-Path $Logs "frontend.pid"

function Stop-Tree([int]$ProcessId) {
  & taskkill.exe /PID $ProcessId /T /F 2>$null | Out-Null
}

if (-not (Test-Path $PidFile)) {
  Write-Host "PID file missing; try port 5173"
  $conn = Get-NetTCPConnection -LocalPort 5173 -State Listen -ErrorAction SilentlyContinue | Select-Object -First 1
  if ($null -eq $conn) {
    Write-Host "frontend not running"
    exit 0
  }
  Stop-Tree -ProcessId ([int]$conn.OwningProcess)
  Write-Host "stopped by port PID=$($conn.OwningProcess)"
  exit 0
}

$oldPid = (Get-Content $PidFile -Raw).Trim()
if ($oldPid -and (Get-Process -Id $oldPid -ErrorAction SilentlyContinue)) {
  Stop-Tree -ProcessId ([int]$oldPid)
  Write-Host "stopped frontend PID=$oldPid"
} else {
  Write-Host "PID=$oldPid not found"
  $conn = Get-NetTCPConnection -LocalPort 5173 -State Listen -ErrorAction SilentlyContinue | Select-Object -First 1
  if ($null -ne $conn) {
    Stop-Tree -ProcessId ([int]$conn.OwningProcess)
    Write-Host "stopped by port PID=$($conn.OwningProcess)"
  }
}
Remove-Item $PidFile -Force -ErrorAction SilentlyContinue
