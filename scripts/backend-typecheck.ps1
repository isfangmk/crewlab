# 后端 basedpyright 类型检查（与前端 npm run typecheck 对应）
$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $PSScriptRoot
Set-Location (Join-Path $root "backend")
if (-not (Test-Path ".venv")) {
    Write-Error "missing .venv; run: uv sync (in backend/)"
}
uv run basedpyright @args
