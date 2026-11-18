# AI workbench service manager: redis (:6379) + sandbox (WSL docker.sock tcp 转发) + backend (uvicorn :18080) + frontend (Vite :5173) + mcp (obs-tools SSE :18099)
# Usage:
#   scripts/service.ps1 start   [backend|frontend|mcp|redis|sandbox|all]   (default all)
#   scripts/service.ps1 stop    [backend|frontend|mcp|redis|sandbox|all]
#   scripts/service.ps1 restart [backend|frontend|mcp|redis|sandbox|all]
#   sandbox 仅在 SANDBOX_EXECUTOR=docker（默认）时启动转发；local 执行器自动跳过。
param(
  [Parameter(Position = 0, Mandatory = $true)]
  [ValidateSet('start', 'stop', 'restart')]
  [string]$Action,

  [Parameter(Position = 1)]
  [ValidateSet('backend', 'frontend', 'mcp', 'redis', 'sandbox', 'all')]
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

# 读 .env 中 SANDBOX_EXECUTOR：未配置视为 docker（与 config.py 默认值一致）。
function Get-SandboxExecutor() {
  $EnvFile = Join-Path $Root "backend\.env"
  if (Test-Path $EnvFile) {
    $line = Get-Content $EnvFile | Where-Object { $_ -match '^\s*SANDBOX_EXECUTOR\s*=' } | Select-Object -Last 1
    if ($line -and $line -match '^\s*SANDBOX_EXECUTOR\s*=\s*"?([^"\s]+)"?') {
      return $matches[1].ToLowerInvariant()
    }
  }
  return 'docker'
}

# 沙箱转发：socat 跑在 WSL 内把 docker.sock 转给 tcp 2375（供 SANDBOX_DOCKER_HOST 连接）。
# 启停逻辑在 scripts/sandbox-proxy.sh（WSL 内执行），PowerShell 只调用脚本，规避 Windows→WSL 的引号/转义问题。
function Get-SandboxProxyScript() {
  # Windows 路径 D:\... → WSL 路径 /mnt/d/...
  $drive = $Root.Substring(0, 1).ToLowerInvariant()
  $rest = $Root.Substring(2).Replace('\', '/')
  return "/mnt/$drive$rest/scripts/sandbox-proxy.sh"
}

function Start-Sandbox() {
  $executor = Get-SandboxExecutor
  if ($executor -ne 'docker') {
    Write-Host "sandbox skipped: SANDBOX_EXECUTOR=$executor (仅 docker 执行器需要转发服务)"
    return
  }
  $script = Get-SandboxProxyScript
  $out = (& wsl.exe -e sh $script start 2>&1 | Out-String).Trim()
  Write-Host $out
  if ($LASTEXITCODE -ne 0 -or $out -match 'ERROR') {
    Write-Error "sandbox start failed: $out"
  }
  # 兼容旧 pid 文件：新方案 pid 由 sh 脚本在 WSL 内维护，清理旧文件避免误导
  Remove-Item (Join-Path $Logs "sandbox.pid") -Force -ErrorAction SilentlyContinue
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
      # 多 worker：架构已全量 Redis 化（帧总线/事件总线/锁/停止/共享状态/通知），
      # 代码层面多 worker 安全。但 Windows 上 uvicorn 的多进程 supervisor 用
      # SO_REUSEPORT 共享监听 socket，Windows 不支持 → 第二个 worker 起不来
      # （OSError WinError 10022），故本地默认单 worker（不传 --workers）。
      # 真正的多 worker 部署放 Linux：设 UVICORN_WORKERS=N 即启用。
      $Workers = $env:UVICORN_WORKERS
      $WorkersArg = ""
      if ($Workers -and ([int]$Workers -gt 1)) {
        $WorkersArg = " --workers $Workers"
      }
      $Arg = "/c `"`"$Uvicorn`" app.main:app --host 127.0.0.1 --port 18080 --log-level info$WorkersArg`""
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
    'mcp' {
      # 可观测 MCP（obs-tools）：SSE :18099，日志重定向到 logs/mcp.log
      $Port = 18099
      $WorkDir = Join-Path $Root "scripts\mcp-obs"
      $Python = Join-Path $Root "backend\.venv\Scripts\python.exe"
      if (-not (Test-Path $Python)) {
        Write-Error "mcp missing backend/.venv; run: uv sync (in backend/)"
      }
      $env:PYTHONUTF8 = "1"
      $LogFile = Join-Path $Logs "mcp.log"
      $Arg = "/c `"`"$Python`" server.py > `"$LogFile`" 2>&1`""
    }
    'redis' {
      # Redis 强依赖：用户自备的 Windows Redis，绑定 127.0.0.1:6379，日志重定向到 logs/redis.log
      $Port = 6379
      $WorkDir = "C:\Users\admin\Downloads\Redis-x64-5.0.14.1"
      $Server = Join-Path $WorkDir "redis-server.exe"
      if (-not (Test-Path $Server)) {
        Write-Error "redis-server.exe not found at $Server; 请自备 Redis 并修改 service.ps1 的 Redis 路径"
      }
      $LogFile = Join-Path $Logs "redis.log"
      $Arg = "/c `"`"$Server`" `"$WorkDir\redis.windows.conf`" --port 6379 --bind 127.0.0.1 > `"$LogFile`" 2>&1`""
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

function Stop-Sandbox() {
  $script = Get-SandboxProxyScript
  $out = (& wsl.exe -e sh $script stop 2>&1 | Out-String).Trim()
  Write-Host $out
  Remove-Item (Join-Path $Logs "sandbox.pid") -Force -ErrorAction SilentlyContinue
}

$ports = @{ backend = 18080; frontend = 5173; mcp = 18099; redis = 6379; sandbox = 0 }
$names = if ($Target -eq 'all') { @('redis', 'sandbox', 'backend', 'frontend', 'mcp') } else { @($Target) }

foreach ($name in $names) {
  # sandbox 进程在 WSL 内、不按 Windows 端口管理，走专用启停并跳过通用端口检查
  if ($name -eq 'sandbox') {
    switch ($Action) {
      'start' { Start-Sandbox }
      'stop' { Stop-Sandbox }
      'restart' { Stop-Sandbox; Start-Sleep -Seconds 1; Start-Sandbox }
    }
    continue
  }
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
