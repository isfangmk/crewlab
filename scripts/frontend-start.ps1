# Start AI workbench frontend (Vite)
$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent $PSScriptRoot
$Web = Join-Path $Root "web"
$Logs = Join-Path $Root "logs"
# PID 在 logs/；日志文件由 Vite customLogger 覆盖写入 logs/frontend.log
$PidFile = Join-Path $Logs "frontend.pid"
New-Item -ItemType Directory -Force -Path $Logs | Out-Null

if (Test-Path $PidFile) {
  $oldPid = (Get-Content $PidFile -Raw).Trim()
  if ($oldPid -and (Get-Process -Id $oldPid -ErrorAction SilentlyContinue)) {
    Write-Error "frontend already running PID=$oldPid; run scripts/frontend-stop.ps1 first"
  }
  Remove-Item $PidFile -Force -ErrorAction SilentlyContinue
}

if (-not (Test-Path (Join-Path $Web "node_modules"))) {
  Write-Error "missing node_modules; run: npm install (in web/)"
}

$Npm = (Get-Command npm.cmd -ErrorAction Stop).Source
$arg = "/c `"`"$Npm`" run dev`""
$proc = Start-Process -FilePath "cmd.exe" `
  -ArgumentList $arg `
  -WorkingDirectory $Web `
  -PassThru `
  -WindowStyle Hidden
Set-Content -Path $PidFile -Value $proc.Id -NoNewline
Write-Host "frontend started PID=$($proc.Id) http://127.0.0.1:5173"
Write-Host "log=$Logs\frontend.log (logger overwrite)"
