# Start AI workbench backend (no --reload; use restart script after code change)
$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent $PSScriptRoot
$Backend = Join-Path $Root "backend"
$Logs = Join-Path $Root "logs"
# PID 在 logs/；日志文件由 Python logging 覆盖写入 logs/backend.log
$PidFile = Join-Path $Logs "backend.pid"
New-Item -ItemType Directory -Force -Path $Logs | Out-Null

if (Test-Path $PidFile) {
  $oldPid = (Get-Content $PidFile -Raw).Trim()
  if ($oldPid -and (Get-Process -Id $oldPid -ErrorAction SilentlyContinue)) {
    Write-Error "backend already running PID=$oldPid; run scripts/backend-stop.ps1 first"
  }
  Remove-Item $PidFile -Force -ErrorAction SilentlyContinue
}

$Uvicorn = Join-Path $Backend ".venv\Scripts\uvicorn.exe"
if (-not (Test-Path $Uvicorn)) {
  Write-Error "missing .venv; run: uv sync (in backend/)"
}

$env:PYTHONUTF8 = "1"
$arg = "/c `"`"$Uvicorn`" app.main:app --host 127.0.0.1 --port 18080 --log-level info`""
$proc = Start-Process -FilePath "cmd.exe" `
  -ArgumentList $arg `
  -WorkingDirectory $Backend `
  -PassThru `
  -WindowStyle Hidden
Set-Content -Path $PidFile -Value $proc.Id -NoNewline
Write-Host "backend started PID=$($proc.Id) http://127.0.0.1:18080"
Write-Host "log=$Logs\backend.log (logger overwrite)"
