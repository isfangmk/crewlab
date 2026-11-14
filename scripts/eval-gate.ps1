# CI 评测门禁：创建评测运行 -> 轮询完成 -> 调门禁 -> 非零退出码
# 用法: powershell -File scripts/eval-gate.ps1 -DatasetId 3 [-BaselineRunUid xxx] [-AgentId default] [-FailOnRegressions]
# 鉴权用 api_tokens 签发的 Bearer：$env:EVAL_GATE_TOKEN 或 -Token 传入
param(
    [Parameter(Mandatory = $true)][int]$DatasetId,
    [string]$BaselineRunUid = "",
    [string]$AgentId = "default",
    [string]$BaseUrl = "http://127.0.0.1:18080",
    [string]$Token = "",
    [switch]$FailOnRegressions,
    [int]$TimeoutSeconds = 1800
)

$ErrorActionPreference = "Stop"

if ($Token -eq "") { $Token = $env:EVAL_GATE_TOKEN }
if ($Token -eq "") {
    Write-Error "缺少 Token：请设置 EVAL_GATE_TOKEN 或传 -Token"
    exit 2
}
$headers = @{ Authorization = "Bearer $Token" }
$api = "$BaseUrl/api/v1"

# 可选：把基线运行写进评测集，门禁与 UI 共用同一份口径
if ($BaselineRunUid -ne "") {
    Invoke-RestMethod -Method Patch -Uri "$api/evals/datasets/$DatasetId" `
        -Headers $headers -ContentType "application/json" `
        -Body (@{ baseline_run_uid = $BaselineRunUid } | ConvertTo-Json) | Out-Null
}

$run = Invoke-RestMethod -Method Post -Uri "$api/evals/datasets/$DatasetId/runs" `
    -Headers $headers -ContentType "application/json" `
    -Body (@{ agent_id = $AgentId; provider = ""; model = "" } | ConvertTo-Json)
$runUid = $run.run_uid
Write-Host "评测运行已发起 run_uid=$runUid"

$deadline = (Get-Date).AddSeconds($TimeoutSeconds)
while ((Get-Date) -lt $deadline) {
    Start-Sleep -Seconds 5
    $detail = Invoke-RestMethod -Method Get -Uri "$api/evals/runs/$runUid" -Headers $headers
    if ($detail.status -ne "running") { break }
}
if ($detail.status -eq "running") {
    Write-Error "评测运行超时 run_uid=$runUid"
    exit 2
}
if ($detail.status -ne "completed") {
    Write-Error "评测运行失败 run_uid=$runUid error=$($detail.error)"
    exit 2
}
Write-Host "评测完成 passed=$($detail.passed_cases)/$($detail.total_cases)"

$gate = Invoke-RestMethod -Method Post -Uri "$api/evals/runs/$runUid/gate" -Headers $headers
if (-not $gate.pass) {
    Write-Host "门禁未通过："
    $gate.reasons | ForEach-Object { Write-Host "  - $_" }
    exit 1
}
if ($FailOnRegressions -and $gate.regressions -gt 0) {
    Write-Host "门禁通过但存在 $($gate.regressions) 条回归（-FailOnRegressions）"
    exit 1
}
Write-Host "门禁通过 pass_rate=$($gate.pass_rate) regressions=$($gate.regressions)"
exit 0
