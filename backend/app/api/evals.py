"""评测集与评测运行 API。

读接口对已登录用户开放；写接口要求非只读角色。评测运行会消耗真实模型，
因此发起前把用例与目标配置冻结进运行记录，结果可按 trace 回溯。
"""

from __future__ import annotations

import csv
import io
import json
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import Response
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import require_auth, require_not_viewer
from app.db import get_db
from app.services import (
    annotation_service,
    eval_gate_service,
    eval_judge_service,
    eval_runner,
    eval_service,
    eval_share_service,
    redteam_library,
)
from app.services.audit_service import write_audit
from app.services.auth_service import AuthUser
from app.services.llm_config import resolve_model
from app.services.redaction import redact_value

router = APIRouter(tags=["evals"])


class DatasetCreateRequest(BaseModel):
    name: str = Field(min_length=1, max_length=128)
    description: str = ""


class DatasetUpdateRequest(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=128)
    description: str | None = None
    # 门禁配置：基线运行 + 回归上限 + 通过率下限
    baseline_run_uid: str | None = None
    gate_max_regressions: int | None = Field(default=None, ge=0)
    gate_min_pass_rate: float | None = Field(default=None, ge=0, le=1)


class CaseCreateRequest(BaseModel):
    name: str = Field(min_length=1, max_length=128)
    input: str = Field(min_length=1)
    kind: str = "normal"
    attack_type: str = ""
    safety_keywords: list[str] = Field(default_factory=list)
    expected_keywords: list[str] = Field(default_factory=list)
    forbidden_keywords: list[str] = Field(default_factory=list)
    expected_tools: list[str] = Field(default_factory=list)
    forbidden_tools: list[str] = Field(default_factory=list)
    max_duration_ms: int | None = Field(default=None, ge=0)
    max_total_tokens: int | None = Field(default=None, ge=0)
    enabled: bool = True
    sort_order: int | None = None


class CaseUpdateRequest(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=128)
    input: str | None = Field(default=None, min_length=1)
    kind: str | None = None
    attack_type: str | None = None
    safety_keywords: list[str] | None = None
    expected_keywords: list[str] | None = None
    forbidden_keywords: list[str] | None = None
    expected_tools: list[str] | None = None
    forbidden_tools: list[str] | None = None
    max_duration_ms: int | None = Field(default=None, ge=0)
    max_total_tokens: int | None = Field(default=None, ge=0)
    enabled: bool | None = None
    sort_order: int | None = None


class RunCreateRequest(BaseModel):
    agent_id: str = Field(min_length=1, max_length=128)
    # 都留空 = 助理模型或全局默认
    provider: str = ""
    model: str = ""


class JudgeConfigRequest(BaseModel):
    enabled: bool = False
    rubric: str = ""
    judge_provider: str = ""
    judge_model: str = ""
    pass_score: float = Field(default=3.0, ge=0, le=eval_judge_service.MAX_SCORE)


class AnnotationCreateRequest(BaseModel):
    result_id: str = Field(min_length=1, max_length=32)
    label: str = Field(min_length=1, max_length=32)
    comment: str = ""


class AnnotationUpdateRequest(BaseModel):
    label: str = Field(min_length=1, max_length=32)
    comment: str = ""


def _dataset_detail(dataset: Any, cases: list[Any], runs: list[Any]) -> dict[str, Any]:
    return {
        **eval_service.dataset_to_wire(dataset),
        "cases": [eval_service.case_to_wire(item) for item in cases],
        "runs": [eval_service.run_to_wire(item) for item in runs],
    }


@router.get("/evals/datasets")
async def list_datasets(
    db: AsyncSession = Depends(get_db),
    _user: AuthUser = Depends(require_auth),
) -> dict:
    items = await eval_service.list_datasets(db)
    return {"items": items, "total": len(items)}


@router.post("/evals/datasets")
async def create_dataset(
    body: DatasetCreateRequest,
    db: AsyncSession = Depends(get_db),
    user: AuthUser = Depends(require_not_viewer),
) -> dict:
    try:
        row = await eval_service.create_dataset(
            db,
            name=body.name,
            description=body.description,
            created_by=user.id,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    await write_audit(
        actor_user_id=user.id,
        action="eval.dataset_create",
        resource_type="eval_dataset",
        resource_id=str(row.id),
        detail={"name": row.name},
    )
    return eval_service.dataset_to_wire(row)


@router.get("/evals/datasets/{dataset_id}")
async def get_dataset_detail(
    dataset_id: int,
    db: AsyncSession = Depends(get_db),
    _user: AuthUser = Depends(require_auth),
) -> dict:
    try:
        dataset = await eval_service.get_dataset(db, dataset_id)
        cases = await eval_service.list_cases(db, dataset_id)
        runs = await eval_service.list_runs(db, dataset_id)
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return _dataset_detail(dataset, cases, runs)


@router.patch("/evals/datasets/{dataset_id}")
async def update_dataset(
    dataset_id: int,
    body: DatasetUpdateRequest,
    db: AsyncSession = Depends(get_db),
    user: AuthUser = Depends(require_not_viewer),
) -> dict:
    try:
        row = await eval_service.update_dataset(
            db,
            dataset_id,
            name=body.name,
            description=body.description,
            baseline_run_uid=body.baseline_run_uid,
            gate_max_regressions=body.gate_max_regressions,
            gate_min_pass_rate=body.gate_min_pass_rate,
        )
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    await write_audit(
        actor_user_id=user.id,
        action="eval.dataset_update",
        resource_type="eval_dataset",
        resource_id=str(row.id),
        detail={"name": row.name},
    )
    return eval_service.dataset_to_wire(row)


@router.delete("/evals/datasets/{dataset_id}")
async def delete_dataset(
    dataset_id: int,
    db: AsyncSession = Depends(get_db),
    user: AuthUser = Depends(require_not_viewer),
) -> dict:
    try:
        row = await eval_service.get_dataset(db, dataset_id)
        name = row.name
        await eval_service.delete_dataset(db, dataset_id)
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    await write_audit(
        actor_user_id=user.id,
        action="eval.dataset_delete",
        resource_type="eval_dataset",
        resource_id=str(dataset_id),
        detail={"name": name},
    )
    return {"ok": True}


@router.post("/evals/datasets/{dataset_id}/cases")
async def create_case(
    dataset_id: int,
    body: CaseCreateRequest,
    db: AsyncSession = Depends(get_db),
    user: AuthUser = Depends(require_not_viewer),
) -> dict:
    try:
        fields = eval_service.validate_case_fields(
            name=body.name,
            input_text=body.input,
            kind=body.kind,
            attack_type=body.attack_type,
            safety_keywords=body.safety_keywords,
            expected_keywords=body.expected_keywords,
            forbidden_keywords=body.forbidden_keywords,
            expected_tools=body.expected_tools,
            forbidden_tools=body.forbidden_tools,
            max_duration_ms=body.max_duration_ms,
            max_total_tokens=body.max_total_tokens,
        )
        row = await eval_service.create_case(
            db,
            dataset_id,
            fields=fields,
            enabled=body.enabled,
            sort_order=body.sort_order,
        )
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    await write_audit(
        actor_user_id=user.id,
        action="eval.case_create",
        resource_type="eval_case",
        resource_id=str(row.id),
        detail={"dataset_id": dataset_id, "name": row.name},
    )
    return eval_service.case_to_wire(row)


@router.patch("/evals/cases/{case_id}")
async def update_case(
    case_id: int,
    body: CaseUpdateRequest,
    db: AsyncSession = Depends(get_db),
    user: AuthUser = Depends(require_not_viewer),
) -> dict:
    try:
        existing = await eval_service.get_case(db, case_id)
        current = eval_service.case_to_wire(existing)
        # 部分更新：请求里没带的字段沿用当前值，再整体走一次校验
        fields = eval_service.validate_case_fields(
            name=body.name if body.name is not None else current["name"],
            input_text=body.input if body.input is not None else current["input"],
            kind=body.kind if body.kind is not None else current["kind"],
            attack_type=(
                body.attack_type
                if body.attack_type is not None
                else current["attack_type"]
            ),
            safety_keywords=(
                body.safety_keywords
                if body.safety_keywords is not None
                else current["safety_keywords"]
            ),
            expected_keywords=(
                body.expected_keywords
                if body.expected_keywords is not None
                else current["expected_keywords"]
            ),
            forbidden_keywords=(
                body.forbidden_keywords
                if body.forbidden_keywords is not None
                else current["forbidden_keywords"]
            ),
            expected_tools=(
                body.expected_tools
                if body.expected_tools is not None
                else current["expected_tools"]
            ),
            forbidden_tools=(
                body.forbidden_tools
                if body.forbidden_tools is not None
                else current["forbidden_tools"]
            ),
            max_duration_ms=(
                body.max_duration_ms
                if "max_duration_ms" in body.model_fields_set
                else current["max_duration_ms"]
            ),
            max_total_tokens=(
                body.max_total_tokens
                if "max_total_tokens" in body.model_fields_set
                else current["max_total_tokens"]
            ),
        )
        row = await eval_service.update_case(
            db,
            case_id,
            fields=fields,
            enabled=body.enabled,
            sort_order=body.sort_order,
        )
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    await write_audit(
        actor_user_id=user.id,
        action="eval.case_update",
        resource_type="eval_case",
        resource_id=str(row.id),
        detail={"dataset_id": row.dataset_id, "name": row.name},
    )
    return eval_service.case_to_wire(row)


@router.delete("/evals/cases/{case_id}")
async def delete_case(
    case_id: int,
    db: AsyncSession = Depends(get_db),
    user: AuthUser = Depends(require_not_viewer),
) -> dict:
    try:
        row = await eval_service.get_case(db, case_id)
        dataset_id = row.dataset_id
        name = row.name
        await eval_service.delete_case(db, case_id)
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    await write_audit(
        actor_user_id=user.id,
        action="eval.case_delete",
        resource_type="eval_case",
        resource_id=str(case_id),
        detail={"dataset_id": dataset_id, "name": name},
    )
    return {"ok": True}


@router.post("/evals/datasets/{dataset_id}/runs")
async def start_run(
    dataset_id: int,
    body: RunCreateRequest,
    db: AsyncSession = Depends(get_db),
    user: AuthUser = Depends(require_not_viewer),
) -> dict:
    try:
        dataset = await eval_service.get_dataset(db, dataset_id)
        agent_id, provider, model = await eval_service.resolve_eval_target(
            db,
            agent_id=body.agent_id,
            provider=body.provider,
            model=body.model,
        )
        run = await eval_service.create_run(
            db,
            dataset=dataset,
            agent_id=agent_id,
            provider=provider,
            model=model,
            created_by=user.id,
        )
    except LookupError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    eval_runner.start_run(run.run_uid)
    await write_audit(
        actor_user_id=user.id,
        action="eval.run",
        resource_type="eval_run",
        resource_id=run.run_uid,
        detail={
            "dataset_id": run.dataset_id,
            "dataset_name": run.dataset_name,
            "agent_id": run.agent_id,
            "provider": run.provider,
            "model": run.model,
            "total_cases": run.total_cases,
        },
    )
    return eval_service.run_to_wire(run)


@router.get("/evals/runs/{run_uid}")
async def get_run(
    run_uid: str,
    db: AsyncSession = Depends(get_db),
    _user: AuthUser = Depends(require_auth),
) -> dict:
    try:
        return await eval_service.get_run_detail(db, run_uid)
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.get("/evals/runs/{run_uid}/compare")
async def compare_run(
    run_uid: str,
    baseline: str = Query(min_length=1),
    db: AsyncSession = Depends(get_db),
    _user: AuthUser = Depends(require_auth),
) -> dict:
    try:
        return await eval_service.compare_runs(db, run_uid, baseline)
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.get("/evals/datasets/{dataset_id}/judge-config")
async def get_judge_config(
    dataset_id: int,
    db: AsyncSession = Depends(get_db),
    _user: AuthUser = Depends(require_auth),
) -> dict:
    try:
        await eval_service.get_dataset(db, dataset_id)
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    config = await eval_judge_service.get_judge_config(db, dataset_id)
    if config is None:
        # 未配置 = 关闭状态的默认口径，前端直接可编辑
        return {
            "dataset_id": dataset_id,
            "enabled": False,
            "rubric": "",
            "judge_provider": "",
            "judge_model": "",
            "pass_score": 3.0,
            "updated_at": None,
        }
    return eval_judge_service.judge_config_to_wire(config)


@router.put("/evals/datasets/{dataset_id}/judge-config")
async def save_judge_config(
    dataset_id: int,
    body: JudgeConfigRequest,
    db: AsyncSession = Depends(get_db),
    user: AuthUser = Depends(require_not_viewer),
) -> dict:
    try:
        await eval_service.get_dataset(db, dataset_id)
        fields = eval_judge_service.validate_judge_fields(
            enabled=body.enabled,
            rubric=body.rubric,
            judge_provider=body.judge_provider,
            judge_model=body.judge_model,
            pass_score=body.pass_score,
        )
        # 指定了裁判模型就先校验可解析，避免运行期才暴雷
        if fields["judge_provider"] != "":
            await resolve_model(db, fields["judge_provider"], fields["judge_model"])
        row = await eval_judge_service.save_judge_config(db, dataset_id, fields=fields)
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    await write_audit(
        actor_user_id=user.id,
        action="eval.judge_config_update",
        resource_type="eval_dataset",
        resource_id=str(dataset_id),
        detail={"enabled": row.enabled, "pass_score": row.pass_score},
    )
    return eval_judge_service.judge_config_to_wire(row)


@router.get("/evals/runs/{run_uid}/results/{result_id}/judge-scores")
async def list_judge_scores(
    run_uid: str,
    result_id: str,
    db: AsyncSession = Depends(get_db),
    _user: AuthUser = Depends(require_auth),
) -> dict:
    try:
        detail = await eval_service.get_run_detail(db, run_uid)
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    if result_id not in {item["id"] for item in detail["results"]}:
        raise HTTPException(status_code=404, detail="评测结果不存在")
    items = await eval_judge_service.list_judge_scores(db, result_id)
    return {"items": items, "total": len(items)}


@router.post("/evals/annotations")
async def create_annotation(
    body: AnnotationCreateRequest,
    db: AsyncSession = Depends(get_db),
    user: AuthUser = Depends(require_not_viewer),
) -> dict:
    try:
        row = await annotation_service.submit_annotation(
            db,
            result_id=body.result_id,
            annotator_user_id=user.id,
            label=body.label,
            comment=body.comment,
        )
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    await write_audit(
        actor_user_id=user.id,
        action="eval.annotation",
        resource_type="eval_annotation",
        resource_id=row.id,
        detail={"result_id": row.result_id, "label": row.label},
    )
    return annotation_service.annotation_to_wire(row)


@router.post("/evals/annotations/{annotation_id}")
async def update_annotation(
    annotation_id: str,
    body: AnnotationUpdateRequest,
    db: AsyncSession = Depends(get_db),
    user: AuthUser = Depends(require_not_viewer),
) -> dict:
    try:
        row = await annotation_service.update_annotation(
            db, annotation_id, label=body.label, comment=body.comment
        )
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    await write_audit(
        actor_user_id=user.id,
        action="eval.annotation",
        resource_type="eval_annotation",
        resource_id=row.id,
        detail={"result_id": row.result_id, "label": row.label},
    )
    return annotation_service.annotation_to_wire(row)


@router.get("/evals/runs/{run_uid}/annotations")
async def list_annotations(
    run_uid: str,
    db: AsyncSession = Depends(get_db),
    _user: AuthUser = Depends(require_auth),
) -> dict:
    try:
        items = await annotation_service.list_annotations(db, run_uid)
        stats = await annotation_service.consistency(db, run_uid)
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return {"items": items, "total": len(items), "consistency": stats}


@router.get("/evals/annotations/queue")
async def claim_next_annotation(
    run_uid: str = Query(min_length=1),
    db: AsyncSession = Depends(get_db),
    user: AuthUser = Depends(require_auth),
) -> dict:
    try:
        item = await annotation_service.claim_next(
            db, run_uid=run_uid, annotator_user_id=user.id
        )
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    if item is None:
        # 队列清空是正常状态，返回空载荷而不是错误
        return {"result": None, "annotation_count": 0}
    return item


@router.post("/evals/runs/{run_uid}/gate")
async def evaluate_gate(
    run_uid: str,
    db: AsyncSession = Depends(get_db),
    user: AuthUser = Depends(require_auth),
) -> dict:
    try:
        result = await eval_gate_service.evaluate_gate(db, run_uid)
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    await write_audit(
        actor_user_id=user.id,
        action="eval.gate",
        resource_type="eval_run",
        resource_id=run_uid,
    )
    return result


@router.get("/evals/runs/{run_uid}/export")
async def export_run(
    run_uid: str,
    format: str = Query(default="json", pattern="^(json|csv)$"),
    db: AsyncSession = Depends(get_db),
    user: AuthUser = Depends(require_auth),
) -> Response:
    try:
        detail = await eval_service.get_run_detail(db, run_uid)
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    await write_audit(
        actor_user_id=user.id,
        action="eval.export",
        resource_type="eval_run",
        resource_id=run_uid,
        detail={"format": format},
    )
    # 导出内容按统一脱敏规则处理，避免把密钥类字段带出系统
    run_meta = {key: value for key, value in detail.items() if key != "results"}
    results = [redact_value(item) for item in detail["results"]]
    if format == "json":
        payload = redact_value({**run_meta, "results": results})
        return Response(
            content=json.dumps(payload, ensure_ascii=False, indent=2),
            media_type="application/json",
            headers={
                "Content-Disposition": f'attachment; filename="eval-{run_uid}.json"'
            },
        )
    columns = [
        "case_name",
        "status",
        "input",
        "output",
        "failure_reasons",
        "tool_names",
        "duration_ms",
        "total_tokens",
        "trace_id",
        "session_id",
        "created_at",
    ]
    buffer = io.StringIO()
    # utf-8-sig 让 Excel 直接双击打开不乱码
    writer = csv.writer(buffer)
    writer.writerow(columns)
    for item in results:
        row = []
        for column in columns:
            value = item.get(column)
            if isinstance(value, list):
                value = " / ".join(str(part) for part in value)
            row.append(value if value is not None else "")
        writer.writerow(row)
    return Response(
        content="\ufeff" + buffer.getvalue(),
        media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": f'attachment; filename="eval-{run_uid}.csv"'},
    )


@router.post("/evals/datasets/{dataset_id}/redteam/seed")
async def seed_redteam_cases(
    dataset_id: int,
    db: AsyncSession = Depends(get_db),
    user: AuthUser = Depends(require_not_viewer),
) -> dict:
    try:
        created = await redteam_library.seed_redteam_cases(db, dataset_id)
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    await write_audit(
        actor_user_id=user.id,
        action="eval.redteam_seed",
        resource_type="eval_dataset",
        resource_id=str(dataset_id),
        detail={"created": len(created)},
    )
    return {"created": len(created), "items": [eval_service.case_to_wire(row) for row in created]}


class ShareCreateRequest(BaseModel):
    expires_in_hours: int = Field(default=24, ge=1, le=720)


@router.post("/evals/runs/{run_uid}/share")
async def create_run_share(
    run_uid: str,
    body: ShareCreateRequest,
    db: AsyncSession = Depends(get_db),
    user: AuthUser = Depends(require_not_viewer),
) -> dict:
    """创建运行只读分享；明文 token 只此一次返回，库里存哈希。"""

    try:
        await eval_service.get_run(db, run_uid)
        raw, row = await eval_share_service.create_eval_share(
            db,
            run_uid=run_uid,
            created_by=user.id,
            expires_in_hours=body.expires_in_hours,
        )
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    await write_audit(
        actor_user_id=user.id,
        action="eval.share",
        resource_type="eval_run",
        resource_id=run_uid,
        detail={"expires_in_hours": body.expires_in_hours},
    )
    return {
        "token": raw,
        "expires_at": row.expires_at.isoformat(),
        "run_uid": run_uid,
    }


@router.get("/evals/shares/{token}")
async def read_run_share(
    token: str,
    db: AsyncSession = Depends(get_db),
) -> dict:
    """只读分享：无需登录；载荷整体脱敏后返回，含内嵌评分与门禁结论。"""

    try:
        share = await eval_share_service.resolve_eval_share(db, token)
        detail = await eval_service.get_run_detail(db, share.run_uid)
        dataset = await eval_service.get_dataset(db, detail["dataset_id"])
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc

    # judge 分内嵌到结果行，公开页一次拿全，不必逐条再调接口
    results: list[dict] = []
    for item in detail["results"]:
        scores = await eval_judge_service.list_judge_scores(db, item["id"])
        results.append({**item, "judge_scores": scores})
    # 门禁 / 标注一致性是可选块：没配基线或没标注就不给
    try:
        gate = await eval_gate_service.evaluate_gate(db, share.run_uid)
    except (LookupError, ValueError):
        gate = None
    stats = await annotation_service.consistency(db, share.run_uid)
    payload = {
        "expires_at": share.expires_at.isoformat(),
        "dataset": eval_service.dataset_to_wire(dataset),
        "run": {
            key: value
            for key, value in detail.items()
            if key not in ("results", "cases_snapshot")
        },
        "cases_snapshot": detail.get("cases_snapshot", []),
        "results": results,
        "gate": gate,
        "consistency": stats,
    }
    return redact_value(payload)
