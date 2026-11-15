# AI workbench service manager: backend (uvicorn :18080) + frontend (Vite :5173)
# Usage:
#   scripts/service.ps1 start   [backend|frontend|all]   (default all)
#   scripts/service.ps1 stop    [backend|frontend|all]
#   scripts/service.ps1 restart [backend|frontend|all]
param(
  [Parameter(Position = 0, Mandatory = $true)]
  [ValidateSet('start', 'stop', 'restart')]
  [string]$Action,

  [Parameter(Position = 1)]
  [ValidateSet('backend', 'frontend', 'all')]
  [string]$Target = 'all'
)

$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent $PSScriptRoot
$Logs = Join-Path $Root "logs"
# PID 在 logs/<name>.pid；日志由各自 logger 覆盖写入 logs/<name>.log
New-Item -ItemType Directory -Force -Path $Logs | Out-Null

function Stop-Tree([int]$ProcessId) {
  # taskkill 对已退出的子进程会写 stderr；ErrorActionPreference=Stop 下会中断脚本，
  # 这里临时降级为 Continue：杀活进程才是目的，stderr 噪音不阻塞启停流程
  $prev = $ErrorActionPreference
  $ErrorActionPreference = 'Continue'
  & taskkill.exe /PID $ProcessId /T /F 2>$null | Out-Null
  $ErrorActionPreference = $prev
}

function Get-PortOwner([int]$Port) {
  return Get-NetTCPConnection -LocalPort $Port -State Listen -ErrorAction SilentlyContinue | Select-Object -First 1
}

function Stop-One([string]$Name, [int]$Port) {
  $PidFile = Join-Path $Logs "$Name.pid"
  if (-not (Test-Path $PidFile)) {
    Write-Host "$Name PID file missing; try port $Port"
    $conn = Get-PortOwner $Port
    if ($null -eq $conn) {
      Write-Host "$Name not running"
      return
    }
    Stop-Tree -ProcessId ([int]$conn.OwningProcess)
    Write-Host "$Name stopped by port PID=$($conn.OwningProcess)"
    return
  }
  $oldPid = (Get-Content $PidFile -Raw).Trim()
  if ($oldPid -and (Get-Process -Id $oldPid -ErrorAction SilentlyContinue)) {
    Stop-Tree -ProcessId ([int]$oldPid)
    Write-Host "$Name stopped PID=$oldPid"
  } else {
    Write-Host "$Name PID=$oldPid not found"
    $conn = Get-PortOwner $Port
    if ($null -ne $conn) {
      Stop-Tree -ProcessId ([int]$conn.OwningProcess)
      Write-Host "$Name stopped by port PID=$($conn.OwningProcess)"
    }
  }
  Remove-Item $PidFile -Force -ErrorAction SilentlyContinue
}

function Start-One([string]$Name) {
  switch ($Name) {
    'backend' {
      $Port = 18080
      $WorkDir = Join-Path $Root "backend"
      $Uvicorn = Join-Path $WorkDir ".venv\Scripts\uvicorn.exe"
      if (-not (Test-Path $Uvicorn)) {
        Write-Error "backend missing .venv; run: uv sync (in backend/)"
      }
      $env:PYTHONUTF8 = "1"
      $Arg = "/c `"`"$Uvicorn`" app.main:app --host 127.0.0.1 --port 18080 --log-level info`""
    }
    'frontend' {
      $Port = 5173
      $WorkDir = Join-Path $Root "web"
      if (-not (Test-Path (Join-Path $WorkDir "node_modules"))) {
        Write-Error "frontend missing node_modules; run: npm install (in web/)"
      }
      $Npm = (Get-Command npm.cmd -ErrorAction Stop).Source
      $Arg = "/c `"`"$Npm`" run dev`""
    }
  }

  $PidFile = Join-Path $Logs "$Name.pid"
  if (Test-Path $PidFile) {
    $oldPid = (Get-Content $PidFile -Raw).Trim()
    if ($oldPid -and (Get-Process -Id $oldPid -ErrorAction SilentlyContinue)) {
      Write-Error "$Name already running PID=$oldPid; run scripts/service.ps1 stop $Name first"
    }
    Remove-Item $PidFile -Force -ErrorAction SilentlyContinue
  }
  # 端口被脚本外实例占用也要拦：Vite 撞端口会静默换端口
  $conn = Get-PortOwner $Port
  if ($null -ne $conn) {
    Write-Error "$Name port $Port already in use PID=$($conn.OwningProcess); run scripts/service.ps1 stop $Name first"
  }

  $proc = Start-Process -FilePath "cmd.exe" `
    -ArgumentList $Arg `
    -WorkingDirectory $WorkDir `
    -PassThru `
    -WindowStyle Hidden
  Set-Content -Path $PidFile -Value $proc.Id -NoNewline
  Write-Host "$Name started PID=$($proc.Id) http://127.0.0.1:$Port"
  Write-Host "log=$Logs\$Name.log (logger overwrite)"
}

$ports = @{ backend = 18080; frontend = 5173 }
$names = if ($Target -eq 'all') { @('backend', 'frontend') } else { @($Target) }

foreach ($name in $names) {
  switch ($Action) {
    'start' { Start-One $name }
    'stop' { Stop-One $name $ports[$name] }
    'restart' {
      Stop-One $name $ports[$name]
      Start-Sleep -Seconds 1
      Start-One $name
    }
  }
}
