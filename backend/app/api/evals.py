"""评测集与评测运行 API。

读接口对已登录用户开放；写接口要求非只读角色。评测运行会消耗真实模型，
因此发起前把用例与目标配置冻结进运行记录，结果可按 trace 回溯。
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import require_auth, require_not_viewer
from app.db import get_db
from app.services import eval_runner, eval_service
from app.services.audit_service import write_audit
from app.services.auth_service import AuthUser

router = APIRouter(tags=["evals"])


class DatasetCreateRequest(BaseModel):
    name: str = Field(min_length=1, max_length=128)
    description: str = ""


class DatasetUpdateRequest(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=128)
    description: str | None = None


class CaseCreateRequest(BaseModel):
    name: str = Field(min_length=1, max_length=128)
    input: str = Field(min_length=1)
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


def _dataset_detail(dataset: Any, cases: list[Any], runs: list[Any]) -> dict[str, Any]:
    return {
        "id": dataset.id,
        "name": dataset.name,
        "description": dataset.description,
        "created_at": dataset.created_at.isoformat() if dataset.created_at else None,
        "updated_at": dataset.updated_at.isoformat() if dataset.updated_at else None,
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
