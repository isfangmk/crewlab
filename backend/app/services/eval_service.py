"""评测集 / 用例的持久化与出参转换。

评测运行本身在 eval_runner 中执行；本模块只负责校验、快照和查询，
保证发起运行的请求不需要等待模型。
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any
from uuid import uuid4

from sqlalchemy import case, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.eval import EvalCase, EvalCaseResult, EvalDataset, EvalRun
from app.services import agent_service
from app.services.llm_config import (
    get_default_selection,
    list_enabled_providers,
    resolve_model,
    split_models,
)

RUN_RUNNING = "running"
RUN_COMPLETED = "completed"
RUN_FAILED = "failed"

CASE_PASSED = "passed"
CASE_FAILED = "failed"
CASE_ERROR = "error"

# 用例类型：normal 常规回归；redteam 对抗（越狱/注入/滥用）
KIND_NORMAL = "normal"
KIND_REDTEAM = "redteam"
CASE_KINDS = (KIND_NORMAL, KIND_REDTEAM)

MAX_CASES = 200
MAX_LIST_ITEMS = 50


def _dump_list(items: list[str]) -> str:
    return json.dumps(items, ensure_ascii=False)


def _load_list(raw: str) -> list[str]:
    data = json.loads(raw)
    if not isinstance(data, list):
        raise TypeError("评测断言必须是 JSON 数组")
    return [str(item) for item in data]


def _normalize_strings(items: list[str], field: str, *, max_items: int = 50) -> list[str]:
    """断言列表：去首尾空白、去重保序；空项显式失败。"""

    out: list[str] = []
    for index, item in enumerate(items):
        value = item.strip()
        if value == "":
            raise ValueError(f"{field}[{index}] 不能为空")
        if len(value) > 200:
            raise ValueError(f"{field}[{index}] 最长 200 个字符")
        if value not in out:
            out.append(value)
    if len(out) > max_items:
        raise ValueError(f"{field} 最多 {max_items} 项")
    return out


def validate_dataset_name(name: str) -> str:
    value = name.strip()
    if value == "":
        raise ValueError("评测集名称不能为空")
    if len(value) > 128:
        raise ValueError("评测集名称最长 128 个字符")
    return value


def validate_case_fields(
    *,
    name: str,
    input_text: str,
    expected_keywords: list[str],
    forbidden_keywords: list[str],
    expected_tools: list[str],
    forbidden_tools: list[str],
    max_duration_ms: int | None,
    max_total_tokens: int | None,
    kind: str = KIND_NORMAL,
    attack_type: str = "",
    safety_keywords: list[str] | None = None,
) -> dict[str, Any]:
    case_name = name.strip()
    if case_name == "":
        raise ValueError("用例名称不能为空")
    if len(case_name) > 128:
        raise ValueError("用例名称最长 128 个字符")
    prompt = input_text.strip()
    if prompt == "":
        raise ValueError("用例输入不能为空")
    if max_duration_ms is not None and max_duration_ms < 0:
        raise ValueError("max_duration_ms 不能为负数")
    if max_total_tokens is not None and max_total_tokens < 0:
        raise ValueError("max_total_tokens 不能为负数")
    clean_kind = kind.strip()
    if clean_kind not in CASE_KINDS:
        raise ValueError(f"kind 只能是 {' / '.join(CASE_KINDS)}")
    clean_attack = attack_type.strip()
    if len(clean_attack) > 64:
        raise ValueError("attack_type 最长 64 个字符")
    # 对抗用例必须标攻击类型，便于筛选与统计
    if clean_kind == KIND_REDTEAM and clean_attack == "":
        raise ValueError("对抗用例必须填写 attack_type")
    return {
        "name": case_name,
        "input": prompt,
        "expected_keywords": _normalize_strings(expected_keywords, "expected_keywords"),
        "forbidden_keywords": _normalize_strings(
            forbidden_keywords, "forbidden_keywords"
        ),
        "expected_tools": _normalize_strings(expected_tools, "expected_tools"),
        "forbidden_tools": _normalize_strings(forbidden_tools, "forbidden_tools"),
        "max_duration_ms": max_duration_ms,
        "max_total_tokens": max_total_tokens,
        "kind": clean_kind,
        "attack_type": clean_attack,
        "safety_keywords": _normalize_strings(
            safety_keywords or [], "safety_keywords"
        ),
    }


def dataset_to_wire(dataset: EvalDataset) -> dict[str, Any]:
    return {
        "id": dataset.id,
        "name": dataset.name,
        "description": dataset.description,
        "baseline_run_uid": dataset.baseline_run_uid or "",
        "gate_max_regressions": dataset.gate_max_regressions,
        "gate_min_pass_rate": dataset.gate_min_pass_rate,
        "created_at": dataset.created_at.isoformat() if dataset.created_at else None,
        "updated_at": dataset.updated_at.isoformat() if dataset.updated_at else None,
    }


def case_to_wire(case: EvalCase) -> dict[str, Any]:
    return {
        "id": case.id,
        "dataset_id": case.dataset_id,
        "name": case.name,
        "input": case.input,
        "kind": case.kind,
        "attack_type": case.attack_type,
        "safety_keywords": _load_list(case.safety_keywords_json),
        "expected_keywords": _load_list(case.expected_keywords_json),
        "forbidden_keywords": _load_list(case.forbidden_keywords_json),
        "expected_tools": _load_list(case.expected_tools_json),
        "forbidden_tools": _load_list(case.forbidden_tools_json),
        "max_duration_ms": case.max_duration_ms,
        "max_total_tokens": case.max_total_tokens,
        "enabled": case.enabled,
        "sort_order": case.sort_order,
        "created_at": case.created_at.isoformat() if case.created_at else None,
        "updated_at": case.updated_at.isoformat() if case.updated_at else None,
    }


def run_to_wire(run: EvalRun) -> dict[str, Any]:
    return {
        "run_uid": run.run_uid,
        "dataset_id": run.dataset_id,
        "dataset_name": run.dataset_name,
        "agent_id": run.agent_id,
        "provider": run.provider,
        "model": run.model,
        "status": run.status,
        "total_cases": run.total_cases,
        "passed_cases": run.passed_cases,
        "failed_cases": run.failed_cases,
        "error": run.error,
        "started_at": run.started_at.isoformat() if run.started_at else None,
        "finished_at": run.finished_at.isoformat() if run.finished_at else None,
    }


def result_to_wire(result: EvalCaseResult) -> dict[str, Any]:
    return {
        "id": result.id,
        "case_id": result.case_id,
        "case_name": result.case_name,
        "input": result.input,
        "status": result.status,
        "output": result.output,
        "failure_reasons": _load_list(result.failure_reasons_json),
        "tool_names": _load_list(result.tool_names_json),
        "trace_id": result.trace_id,
        "session_id": result.session_id,
        "duration_ms": result.duration_ms,
        "total_tokens": result.total_tokens,
        "created_at": result.created_at.isoformat() if result.created_at else None,
    }


def snapshot_case(case: EvalCase) -> dict[str, Any]:
    """发起运行时冻结用例，后续编辑不影响本次结果解释。"""

    return {
        "case_id": case.id,
        "name": case.name,
        "input": case.input,
        "kind": case.kind,
        "attack_type": case.attack_type,
        "safety_keywords": _load_list(case.safety_keywords_json),
        "expected_keywords": _load_list(case.expected_keywords_json),
        "forbidden_keywords": _load_list(case.forbidden_keywords_json),
        "expected_tools": _load_list(case.expected_tools_json),
        "forbidden_tools": _load_list(case.forbidden_tools_json),
        "max_duration_ms": case.max_duration_ms,
        "max_total_tokens": case.max_total_tokens,
    }


async def _ensure_dataset_name_free(
    db: AsyncSession, name: str, exclude_id: int | None = None
) -> None:
    row = await db.scalar(select(EvalDataset).where(EvalDataset.name == name))
    if row is not None and row.id != exclude_id:
        raise ValueError(f"评测集名称已存在: {name}")


async def list_datasets(db: AsyncSession) -> list[dict[str, Any]]:
    datasets = list(
        (
            await db.scalars(
                select(EvalDataset).order_by(
                    EvalDataset.updated_at.desc(), EvalDataset.id.desc()
                )
            )
        ).all()
    )
    if len(datasets) == 0:
        return []

    counts = {
        row[0]: (int(row[1]), int(row[2] or 0))
        for row in (
            await db.execute(
                select(
                    EvalCase.dataset_id,
                    func.count(EvalCase.id),
                    func.sum(case((EvalCase.enabled.is_(True), 1), else_=0)),
                )
                .where(EvalCase.dataset_id.in_([item.id for item in datasets]))
                .group_by(EvalCase.dataset_id)
            )
        ).all()
    }
    latest: dict[int, EvalRun] = {}
    for dataset_id in [item.id for item in datasets]:
        run = await db.scalar(
            select(EvalRun)
            .where(EvalRun.dataset_id == dataset_id)
            .order_by(EvalRun.started_at.desc(), EvalRun.run_uid.desc())
            .limit(1)
        )
        if run is not None:
            latest[dataset_id] = run

    out: list[dict[str, Any]] = []
    for dataset in datasets:
        total, enabled = counts.get(dataset.id, (0, 0))
        last_run = latest.get(dataset.id)
        out.append(
            {
                "id": dataset.id,
                "name": dataset.name,
                "description": dataset.description,
                "baseline_run_uid": dataset.baseline_run_uid or "",
                "gate_max_regressions": dataset.gate_max_regressions,
                "gate_min_pass_rate": dataset.gate_min_pass_rate,
                "case_count": total,
                "enabled_case_count": enabled,
                "created_at": (
                    dataset.created_at.isoformat() if dataset.created_at else None
                ),
                "updated_at": (
                    dataset.updated_at.isoformat() if dataset.updated_at else None
                ),
                "last_run": run_to_wire(last_run) if last_run is not None else None,
            }
        )
    return out


async def get_dataset(db: AsyncSession, dataset_id: int) -> EvalDataset:
    row = await db.get(EvalDataset, dataset_id)
    if row is None:
        raise LookupError(f"评测集不存在: {dataset_id}")
    return row


async def create_dataset(
    db: AsyncSession,
    *,
    name: str,
    description: str,
    created_by: int | None,
) -> EvalDataset:
    clean_name = validate_dataset_name(name)
    await _ensure_dataset_name_free(db, clean_name)
    row = EvalDataset(
        name=clean_name,
        description=description.strip(),
        created_by=created_by,
    )
    db.add(row)
    await db.commit()
    await db.refresh(row)
    return row


def validate_gate_fields(
    *,
    baseline_run_uid: str | None,
    gate_max_regressions: int | None,
    gate_min_pass_rate: float | None,
) -> dict[str, Any]:
    """门禁配置校验：阈值范围合法；基线运行存在性由调用方查库确认。"""

    out: dict[str, Any] = {}
    if baseline_run_uid is not None:
        out["baseline_run_uid"] = baseline_run_uid.strip()
    if gate_max_regressions is not None:
        if gate_max_regressions < 0:
            raise ValueError("gate_max_regressions 不能为负数")
        out["gate_max_regressions"] = gate_max_regressions
    if gate_min_pass_rate is not None:
        if gate_min_pass_rate < 0 or gate_min_pass_rate > 1:
            raise ValueError("gate_min_pass_rate 必须在 0-1 之间")
        out["gate_min_pass_rate"] = float(gate_min_pass_rate)
    return out


async def update_dataset(
    db: AsyncSession,
    dataset_id: int,
    *,
    name: str | None,
    description: str | None,
    baseline_run_uid: str | None = None,
    gate_max_regressions: int | None = None,
    gate_min_pass_rate: float | None = None,
) -> EvalDataset:
    row = await get_dataset(db, dataset_id)
    if name is not None:
        clean_name = validate_dataset_name(name)
        if clean_name != row.name:
            await _ensure_dataset_name_free(db, clean_name, exclude_id=row.id)
            row.name = clean_name
    if description is not None:
        row.description = description.strip()
    gate = validate_gate_fields(
        baseline_run_uid=baseline_run_uid,
        gate_max_regressions=gate_max_regressions,
        gate_min_pass_rate=gate_min_pass_rate,
    )
    if "baseline_run_uid" in gate:
        uid = gate["baseline_run_uid"]
        if uid != "":
            # 基线必须是本评测集的运行，否则回归对比无意义
            run = await db.get(EvalRun, uid)
            if run is None or run.dataset_id != row.id:
                raise ValueError("基线运行不存在或不属于该评测集")
        row.baseline_run_uid = uid or None
    if "gate_max_regressions" in gate:
        row.gate_max_regressions = gate["gate_max_regressions"]
    if "gate_min_pass_rate" in gate:
        row.gate_min_pass_rate = gate["gate_min_pass_rate"]
    await db.commit()
    await db.refresh(row)
    return row


async def delete_dataset(db: AsyncSession, dataset_id: int) -> None:
    row = await get_dataset(db, dataset_id)
    running = await db.scalar(
        select(func.count(EvalRun.run_uid)).where(
            EvalRun.dataset_id == dataset_id, EvalRun.status == RUN_RUNNING
        )
    )
    if int(running or 0) > 0:
        raise ValueError("评测集正在运行，不能删除")
    await db.delete(row)
    await db.commit()


async def list_cases(db: AsyncSession, dataset_id: int) -> list[EvalCase]:
    await get_dataset(db, dataset_id)
    return list(
        (
            await db.scalars(
                select(EvalCase)
                .where(EvalCase.dataset_id == dataset_id)
                .order_by(EvalCase.sort_order, EvalCase.id)
            )
        ).all()
    )


async def get_case(db: AsyncSession, case_id: int) -> EvalCase:
    row = await db.get(EvalCase, case_id)
    if row is None:
        raise LookupError(f"评测用例不存在: {case_id}")
    return row


def _apply_case_fields(row: EvalCase, fields: dict[str, Any]) -> None:
    row.name = fields["name"]
    row.input = fields["input"]
    row.kind = fields["kind"]
    row.attack_type = fields["attack_type"]
    row.safety_keywords_json = _dump_list(fields["safety_keywords"])
    row.expected_keywords_json = _dump_list(fields["expected_keywords"])
    row.forbidden_keywords_json = _dump_list(fields["forbidden_keywords"])
    row.expected_tools_json = _dump_list(fields["expected_tools"])
    row.forbidden_tools_json = _dump_list(fields["forbidden_tools"])
    row.max_duration_ms = fields["max_duration_ms"]
    row.max_total_tokens = fields["max_total_tokens"]


async def create_case(
    db: AsyncSession,
    dataset_id: int,
    *,
    fields: dict[str, Any],
    enabled: bool,
    sort_order: int | None,
) -> EvalCase:
    await get_dataset(db, dataset_id)
    count = await db.scalar(
        select(func.count(EvalCase.id)).where(EvalCase.dataset_id == dataset_id)
    )
    if int(count or 0) >= MAX_CASES:
        raise ValueError(f"单个评测集最多 {MAX_CASES} 条用例")
    if sort_order is None:
        max_order = await db.scalar(
            select(func.max(EvalCase.sort_order)).where(
                EvalCase.dataset_id == dataset_id
            )
        )
        sort_order = int(max_order or 0) + 10
    row = EvalCase(
        dataset_id=dataset_id,
        enabled=enabled,
        sort_order=sort_order,
    )
    _apply_case_fields(row, fields)
    db.add(row)
    await db.commit()
    await db.refresh(row)
    return row


async def update_case(
    db: AsyncSession,
    case_id: int,
    *,
    fields: dict[str, Any] | None,
    enabled: bool | None,
    sort_order: int | None,
) -> EvalCase:
    row = await get_case(db, case_id)
    if fields is not None:
        _apply_case_fields(row, fields)
    if enabled is not None:
        row.enabled = enabled
    if sort_order is not None:
        row.sort_order = sort_order
    await db.commit()
    await db.refresh(row)
    return row


async def delete_case(db: AsyncSession, case_id: int) -> None:
    row = await get_case(db, case_id)
    await db.delete(row)
    await db.commit()


async def resolve_eval_target(
    db: AsyncSession,
    *,
    agent_id: str,
    provider: str,
    model: str,
) -> tuple[str, str, str]:
    """解析评测目标：返回 (agent_id, provider, model)。

    显式给了 provider/model 就按配置校验；都留空时优先助理自己的模型，
    助理未配置模型才落到全局默认。只给一半是配置歧义，直接失败。
    """

    await agent_service.get_agent(db, agent_id)
    has_provider = provider.strip() != ""
    has_model = model.strip() != ""
    if has_provider != has_model:
        raise ValueError("provider 与 model 必须同时提供或同时留空")
    if has_provider:
        resolved = await resolve_model(db, provider.strip(), model.strip())
        return agent_id, resolved.provider_id, model.strip()

    agent = await agent_service.get_agent(db, agent_id)
    if agent.model != "":
        for candidate in await list_enabled_providers(db):
            if agent.model in split_models(candidate.models):
                return agent_id, candidate.provider_id, agent.model
        raise LookupError(f"助理模型不在任何已启用 provider 中: {agent.model}")
    resolved_provider, resolved_model = await get_default_selection(db)
    return agent_id, resolved_provider, resolved_model


async def create_run(
    db: AsyncSession,
    *,
    dataset: EvalDataset,
    agent_id: str,
    provider: str,
    model: str,
    created_by: int | None,
) -> EvalRun:
    running = await db.scalar(
        select(func.count(EvalRun.run_uid)).where(
            EvalRun.dataset_id == dataset.id, EvalRun.status == RUN_RUNNING
        )
    )
    if int(running or 0) > 0:
        raise ValueError("该评测集已有运行中的评测，请等待完成")
    cases = list(
        (
            await db.scalars(
                select(EvalCase)
                .where(
                    EvalCase.dataset_id == dataset.id, EvalCase.enabled.is_(True)
                )
                .order_by(EvalCase.sort_order, EvalCase.id)
            )
        ).all()
    )
    if len(cases) == 0:
        raise ValueError("评测集没有启用的用例")
    snapshots = [snapshot_case(case) for case in cases]
    run = EvalRun(
        run_uid=uuid4().hex,
        dataset_id=dataset.id,
        dataset_name=dataset.name,
        agent_id=agent_id,
        provider=provider,
        model=model,
        status=RUN_RUNNING,
        total_cases=len(snapshots),
        cases_snapshot_json=json.dumps(snapshots, ensure_ascii=False),
        created_by=created_by,
    )
    db.add(run)
    await db.commit()
    await db.refresh(run)
    return run


async def list_runs(
    db: AsyncSession, dataset_id: int, *, limit: int = MAX_LIST_ITEMS
) -> list[EvalRun]:
    await get_dataset(db, dataset_id)
    return list(
        (
            await db.scalars(
                select(EvalRun)
                .where(EvalRun.dataset_id == dataset_id)
                .order_by(EvalRun.started_at.desc(), EvalRun.run_uid.desc())
                .limit(limit)
            )
        ).all()
    )


async def get_run(db: AsyncSession, run_uid: str) -> EvalRun:
    row = await db.get(EvalRun, run_uid)
    if row is None:
        raise LookupError(f"评测运行不存在: {run_uid}")
    return row


async def get_run_detail(db: AsyncSession, run_uid: str) -> dict[str, Any]:
    run = await get_run(db, run_uid)
    results = list(
        (
            await db.scalars(
                select(EvalCaseResult)
                .where(EvalCaseResult.run_uid == run_uid)
                .order_by(EvalCaseResult.created_at, EvalCaseResult.id)
            )
        ).all()
    )
    return {
        **run_to_wire(run),
        "cases_snapshot": json.loads(run.cases_snapshot_json),
        "results": [result_to_wire(item) for item in results],
    }


def _result_key(result: dict[str, Any]) -> str:
    if result["case_id"] is not None:
        return f"id:{result['case_id']}"
    return f"name:{result['case_name']}"


async def compare_runs(
    db: AsyncSession, run_uid: str, baseline_uid: str
) -> dict[str, Any]:
    """两个运行按用例对比；只允许同一评测集，避免拿不同题目算通过率。"""

    if run_uid == baseline_uid:
        raise ValueError("对比的两个运行不能相同")
    current = await get_run_detail(db, run_uid)
    baseline = await get_run_detail(db, baseline_uid)
    if current["dataset_id"] != baseline["dataset_id"]:
        raise ValueError("只能对比同一评测集的运行")

    current_map = {_result_key(item): item for item in current["results"]}
    baseline_map = {_result_key(item): item for item in baseline["results"]}
    keys: list[str] = []
    for item in current["results"]:
        key = _result_key(item)
        if key not in keys:
            keys.append(key)
    for item in baseline["results"]:
        key = _result_key(item)
        if key not in keys:
            keys.append(key)

    rows: list[dict[str, Any]] = []
    improved = 0
    regressed = 0
    for key in keys:
        now = current_map.get(key)
        before = baseline_map.get(key)
        now_status = now["status"] if now is not None else "missing"
        before_status = before["status"] if before is not None else "missing"
        change = "same"
        if before_status != CASE_PASSED and now_status == CASE_PASSED:
            change = "improved"
            improved += 1
        elif before_status == CASE_PASSED and now_status != CASE_PASSED:
            change = "regressed"
            regressed += 1
        rows.append(
            {
                "case_id": (now or before or {}).get("case_id"),
                "case_name": (now or before or {}).get("case_name", ""),
                "baseline_status": before_status,
                "current_status": now_status,
                "change": change,
                "baseline_reasons": before["failure_reasons"] if before else [],
                "current_reasons": now["failure_reasons"] if now else [],
                "baseline_duration_ms": before["duration_ms"] if before else None,
                "current_duration_ms": now["duration_ms"] if now else None,
                "baseline_total_tokens": before["total_tokens"] if before else None,
                "current_total_tokens": now["total_tokens"] if now else None,
            }
        )

    def _sum(values: list[int | None]) -> int:
        return sum(item for item in values if isinstance(item, int))

    return {
        "current": run_to_wire(await get_run(db, run_uid)),
        "baseline": {key: value for key, value in baseline.items() if key != "results"},
        "summary": {
            "improved": improved,
            "regressed": regressed,
            "unchanged": len(rows) - improved - regressed,
            "baseline_passed": baseline["passed_cases"],
            "current_passed": current["passed_cases"],
            "baseline_total_tokens": _sum(
                [item["total_tokens"] for item in baseline["results"]]
            ),
            "current_total_tokens": _sum(
                [item["total_tokens"] for item in current["results"]]
            ),
        },
        "cases": rows,
    }


def finish_run_now(run: EvalRun, status: str, error: str = "") -> None:
    run.status = status
    run.error = error[:2000]
    run.finished_at = datetime.now(timezone.utc)
