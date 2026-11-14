"""评测门禁：与基线运行对比，回归数 / 通过率超阈值即判 fail。

门禁只读已有的运行对比结果做判定，不重复跑评测；基线与阈值配置
在评测集上维护，方便 CI 复用同一份口径。
"""

from __future__ import annotations

from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from app.services import eval_service


async def evaluate_gate(db: AsyncSession, run_uid: str) -> dict[str, Any]:
    """按评测集门禁配置判定一次运行；返回判定明细供 CI / 前端展示。"""

    run = await eval_service.get_run(db, run_uid)
    if run.status != eval_service.RUN_COMPLETED:
        raise ValueError("评测运行尚未完成，无法执行门禁")
    dataset = await eval_service.get_dataset(db, run.dataset_id)
    baseline_uid = dataset.baseline_run_uid or ""
    if baseline_uid == "":
        raise ValueError("该评测集未配置门禁基线运行")

    if run_uid == baseline_uid:
        # 基线自己对自己没有回归可言，只按通过率判
        regressions = 0
        improved = 0
        regressed_cases: list[dict[str, Any]] = []
    else:
        comparison = await eval_service.compare_runs(db, run_uid, baseline_uid)
        summary = comparison["summary"]
        regressions = int(summary["regressed"])
        improved = int(summary["improved"])
        regressed_cases = [
            item for item in comparison["cases"] if item["change"] == "regressed"
        ]
    total = int(run.total_cases or 0)
    pass_rate = (run.passed_cases / total) if total > 0 else 0.0

    reasons: list[str] = []
    if regressions > dataset.gate_max_regressions:
        reasons.append(
            f"回归用例 {regressions} 条，超过允许上限 {dataset.gate_max_regressions} 条"
        )
    if pass_rate < dataset.gate_min_pass_rate:
        reasons.append(
            f"通过率 {pass_rate:.2%} 低于下限 {dataset.gate_min_pass_rate:.2%}"
        )

    return {
        "run_uid": run_uid,
        "baseline_run_uid": baseline_uid,
        "pass": len(reasons) == 0,
        "pass_rate": pass_rate,
        "regressions": regressions,
        "improved": improved,
        "max_regressions": dataset.gate_max_regressions,
        "min_pass_rate": dataset.gate_min_pass_rate,
        "reasons": reasons,
        "regressed_cases": regressed_cases,
    }
