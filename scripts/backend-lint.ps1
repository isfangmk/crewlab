# 后端 ruff 静态检查（与前端 npm run lint 对应）
$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $PSScriptRoot
Set-Location (Join-Path $root "backend")
if (-not (Test-Path ".venv")) {
    Write-Error "missing .venv; run: uv sync (in backend/)"
}
uv run ruff check app tests @args
