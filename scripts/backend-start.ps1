# Start AI workbench backend (no --reload; use restart script after code change)
$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent $PSScriptRoot
$Backend = Join-Path $Root "backend"
$PidFile = Join-Path $Backend ".uvicorn.pid"
$OutLog = Join-Path $Backend "uvicorn.out.log"
$ErrLog = Join-Path $Backend "uvicorn.err.log"

if (Test-Path $PidFile) {
  $oldPid = (Get-Content $PidFile -Raw).Trim()
  if ($oldPid -and (Get-Process -Id $oldPid -ErrorAction SilentlyContinue)) {
    Write-Error "backend already running PID=$oldPid; run scripts/backend-stop.ps1 first"
  }
  Remove-Item $PidFile -Force -ErrorAction SilentlyContinue
}

Push-Location $Backend
try {
  if (-not (Test-Path ".\.venv\Scripts\uvicorn.exe")) {
    Write-Error "missing .venv; run: uv sync (in backend/)"
  }
  $env:PYTHONUTF8 = "1"
  $proc = Start-Process -FilePath ".\.venv\Scripts\uvicorn.exe" `
    -ArgumentList @("app.main:app", "--host", "127.0.0.1", "--port", "18080", "--log-level", "info") `
    -WorkingDirectory $Backend `
    -RedirectStandardOutput $OutLog `
    -RedirectStandardError $ErrLog `
    -PassThru `
    -WindowStyle Hidden
  Set-Content -Path $PidFile -Value $proc.Id -NoNewline
  Write-Host "backend started PID=$($proc.Id) http://127.0.0.1:18080"
  Write-Host "stdout=$OutLog"
  Write-Host "stderr=$ErrLog"
}
finally {
  Pop-Location
}
