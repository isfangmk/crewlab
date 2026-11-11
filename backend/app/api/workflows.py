"""工作流 CRUD、校验与运行 API。"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import require_auth
from app.db import get_db
from app.models_orm.workflow import Workflow as WorkflowRow
from app.models_orm.workflow import WorkflowRun as WorkflowRunRow
from app.schemas.workflow import (
    NODE_GROUPS,
    NODE_KINDS,
    WorkflowDefinition,
    graph_warnings,
    node_group_title,
    node_kind_title,
    parse_definition,
)
from app.services import workflow_service
from app.services.audit_service import write_audit
from app.services.auth_service import AuthUser

logger = logging.getLogger(__name__)

router = APIRouter(tags=["workflows"])


class WorkflowInput(BaseModel):
    """画布保存的入参。

    状态与版本都不由调用方决定：画布保存永远是草稿，发布走单独接口。
    """

    name: str = Field(min_length=1, max_length=128)
    description: str = ""
    definition: WorkflowDefinition


class WorkflowDefinitionInput(BaseModel):
    definition: WorkflowDefinition


class RunInput(BaseModel):
    """运行输入：按开始节点声明的变量名传值。"""

    values: dict[str, Any] = Field(default_factory=dict)


class ResumeInput(BaseModel):
    """人工节点恢复：approve 继续，reject 终止；input 节点用 values 提交。"""

    decision: str = "approve"
    values: dict[str, Any] = Field(default_factory=dict)


def _row_to_wire(
    row: WorkflowRow, *, include_definition: bool, last_run: Any | None
) -> dict[str, Any]:
    definition = parse_definition(row.definition_json)
    payload: dict[str, Any] = {
        "id": row.id,
        "name": row.name,
        "description": row.description,
        "status": row.status,
        "version": row.version,
        "validation_error": row.validation_error,
        "node_count": len(definition.nodes),
        "edge_count": len(definition.edges),
        "warnings": graph_warnings(definition),
        # 最近一次运行的状态与标识：从未运行时为 null
        "last_run": None
        if last_run is None
        else {
            "run_uid": last_run.run_uid,
            "status": last_run.status,
            "created_at": last_run.created_at.isoformat(),
        },
        "created_at": row.created_at.isoformat(),
        "updated_at": row.updated_at.isoformat(),
    }
    if include_definition:
        payload["definition"] = definition.model_dump()
    return payload


def _run_to_wire(row: Any, workflow_name: str) -> dict[str, Any]:
    return {
        "id": row.id,
        "run_uid": row.run_uid,
        "workflow_id": row.workflow_id,
        "workflow_name": workflow_name,
        "workflow_version": row.workflow_version,
        "status": row.status,
        "trigger": row.trigger,
        "triggered_by": row.triggered_by,
        "input": row.input_json,
        "output": row.output_json,
        "error": row.error,
        "duration_ms": row.duration_ms,
        "created_at": row.created_at.isoformat(),
        "finished_at": None if row.finished_at is None else row.finished_at.isoformat(),
    }


async def _workflow_names(db: AsyncSession, ids: set[int]) -> dict[int, str]:
    """运行列表要显示工作流名：一次查回来，避免每行一次查询。"""

    if len(ids) == 0:
        return {}
    result = await db.execute(
        select(WorkflowRow.id, WorkflowRow.name).where(WorkflowRow.id.in_(ids))
    )
    return {row_id: name for row_id, name in result.all()}


async def _workflow_name(db: AsyncSession, workflow_id: int) -> str:
    """单次运行的工作流名：运行随工作流级联删除，查不到说明数据不一致。"""

    name = await db.scalar(select(WorkflowRow.name).where(WorkflowRow.id == workflow_id))
    if name is None:
        raise HTTPException(status_code=500, detail=f"运行引用的工作流不存在: {workflow_id}")
    return name


async def _latest_runs(db: AsyncSession, ids: set[int]) -> dict[int, Any]:
    """每个工作流最近一次运行：列表要展示运行状态，一次查完避免逐行查询。"""

    if len(ids) == 0:
        return {}
    latest_ids = (
        select(func.max(WorkflowRunRow.id))
        .where(WorkflowRunRow.workflow_id.in_(ids))
        .group_by(WorkflowRunRow.workflow_id)
    )
    rows = (
        await db.scalars(select(WorkflowRunRow).where(WorkflowRunRow.id.in_(latest_ids)))
    ).all()
    return {run.workflow_id: run for run in rows}


async def _latest_run(db: AsyncSession, workflow_id: int) -> Any | None:
    """单个工作流最近一次运行。"""

    return (await _latest_runs(db, {workflow_id})).get(workflow_id)


async def _get_row(db: AsyncSession, workflow_id: int) -> WorkflowRow:
    row = await db.get(WorkflowRow, workflow_id)
    if row is None:
        raise HTTPException(status_code=404, detail=f"工作流不存在: {workflow_id}")
    return row


@router.get("/workflows/catalog")
async def workflow_catalog(
    db: AsyncSession = Depends(get_db),
    _user: str = Depends(require_auth),
) -> dict:
    """画布可用的节点类型、分组，以及模型与 MCP 工具。"""

    from app.services import mcp_service
    from app.services.llm_config import list_enabled_providers, split_models

    servers = await mcp_service.list_enabled_servers(db)
    mcp_tools: list[dict[str, str]] = []
    for server in servers:
        for tool in _server_tools(server):
            mcp_tools.append(
                {
                    "server": server.name,
                    "tool": tool,
                    "title": f"{server.name} · {tool}",
                }
            )
    # LLM 节点直接选模型：把每个 provider 的模型摊平给前端
    models: list[dict[str, str]] = []
    for provider in await list_enabled_providers(db):
        for name in split_models(provider.models):
            models.append(
                {
                    "provider": provider.provider_id,
                    "model": name,
                    "title": f"{provider.provider_id} · {name}",
                }
            )
    return {
        "node_groups": [node_group_title(key) for key in NODE_GROUPS],
        "node_kinds": [node_kind_title(key) for key in NODE_KINDS],
        "models": models,
        "mcp_tools": mcp_tools,
    }


def _server_tools(server: Any) -> list[str]:
    import json

    raw = server.tools_cache_json
    if raw == "":
        return []
    data = json.loads(raw)
    if not isinstance(data, list):
        raise TypeError(f"MCP Server {server.name} 的 tools_cache_json 必须是数组")
    names: list[str] = []
    for item in data:
        if not isinstance(item, dict):
            continue
        name = item.get("name")
        if isinstance(name, str) and name != "":
            names.append(name)
    return names


@router.get("/workflows")
async def list_workflows(
    q: str = "",
    status: str = "",
    sort: str = "desc",
    limit: int = Query(default=20, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
    db: AsyncSession = Depends(get_db),
    _user: str = Depends(require_auth),
) -> dict:
    """画布列表：关键字/状态筛选 + 按更新时间排序 + 分页，total 是筛选后的总数。"""

    if status not in ("", "draft", "published"):
        raise HTTPException(status_code=400, detail=f"status 非法: {status}")
    if sort not in ("asc", "desc"):
        raise HTTPException(status_code=400, detail=f"sort 非法: {sort}")

    conditions: list[Any] = []
    keyword = q.strip()
    if keyword != "":
        conditions.append(
            or_(
                WorkflowRow.name.ilike(f"%{keyword}%"),
                WorkflowRow.description.ilike(f"%{keyword}%"),
            )
        )
    if status != "":
        conditions.append(WorkflowRow.status == status)

    count_stmt = select(func.count()).select_from(WorkflowRow)
    list_stmt = select(WorkflowRow)
    for condition in conditions:
        count_stmt = count_stmt.where(condition)
        list_stmt = list_stmt.where(condition)
    total = int(await db.scalar(count_stmt) or 0)

    order = (
        WorkflowRow.updated_at.asc() if sort == "asc" else WorkflowRow.updated_at.desc()
    )
    rows = list(
        (
            await db.scalars(
                list_stmt.order_by(order, WorkflowRow.id.desc())
                .limit(limit)
                .offset(offset)
            )
        ).all()
    )
    latest = await _latest_runs(db, {row.id for row in rows})
    return {
        "items": [
            _row_to_wire(r, include_definition=False, last_run=latest.get(r.id))
            for r in rows
        ],
        "total": total,
    }


@router.post("/workflows")
async def create_workflow(
    body: WorkflowInput,
    db: AsyncSession = Depends(get_db),
    user: AuthUser = Depends(require_auth),
) -> dict:
    existing = await db.scalar(select(WorkflowRow).where(WorkflowRow.name == body.name))
    if existing is not None:
        raise HTTPException(status_code=400, detail=f"工作流名称已存在: {body.name}")
    row = WorkflowRow(
        name=body.name,
        description=body.description,
        definition_json=body.definition.model_dump_json(),
        # 新建的画布是草稿：v1 只是画布自身的版本号，发布后才算「已发布」
        status="draft",
        version=1,
    )
    db.add(row)
    await db.commit()
    await db.refresh(row)
    await write_audit(
        db,
        actor_user_id=user.id,
        action="workflow.create",
        resource_type="workflow",
        resource_id=str(row.id),
        detail={"name": row.name, "nodes": len(body.definition.nodes)},
    )
    logger.info("创建工作流 id=%s name=%s nodes=%s", row.id, row.name, len(body.definition.nodes))
    return _row_to_wire(row, include_definition=True, last_run=await _latest_run(db, row.id))


@router.post("/workflows/validate")
async def validate_workflow(body: WorkflowDefinitionInput) -> dict:
    """画布校验：结构 + ADK 图可执行性。不通过时返回错误，不抛 500。"""

    from app.adk_runtime.workflow_graph import validate

    try:
        validate(body.definition)
    except Exception as exc:  # ADK 抛 GraphValidationError 等，统一转成可读错误
        logger.info("工作流校验不通过: %s", exc)
        return {"ok": False, "error": str(exc), "warnings": graph_warnings(body.definition)}
    return {"ok": True, "error": "", "warnings": graph_warnings(body.definition)}


@router.get("/workflows/runs/list")
async def list_runs(
    workflow_id: int | None = None,
    limit: int = Query(default=20, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
    db: AsyncSession = Depends(get_db),
    _user: str = Depends(require_auth),
) -> dict:
    rows, total = await workflow_service.list_runs(
        db, workflow_id=workflow_id, limit=limit, offset=offset
    )
    names = await _workflow_names(db, {row.workflow_id for row in rows})
    return {
        "items": [_run_to_wire(r, names[r.workflow_id]) for r in rows],
        "total": total,
    }


@router.get("/workflows/runs/{run_uid}")
async def get_run(
    run_uid: str,
    db: AsyncSession = Depends(get_db),
    _user: str = Depends(require_auth),
) -> dict:
    try:
        run = await workflow_service.get_run(db, run_uid)
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    nodes = await workflow_service.list_node_runs(db, run.id)
    payload = _run_to_wire(run, await _workflow_name(db, run.workflow_id))
    payload["nodes"] = [workflow_service.node_run_to_wire(n) for n in nodes]
    payload["definition"] = parse_definition(run.definition_json).model_dump()
    return payload


@router.post("/workflows/runs/{run_uid}/resume")
async def resume_run(
    run_uid: str,
    body: ResumeInput,
    db: AsyncSession = Depends(get_db),
    user: AuthUser = Depends(require_auth),
) -> dict:
    try:
        run = await workflow_service.get_run(db, run_uid)
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    if body.decision not in ("approve", "reject"):
        raise HTTPException(status_code=400, detail=f"decision 非法: {body.decision}")
    try:
        await workflow_service.resume_run(
            db, run=run, decision=body.decision, payload=body.values
        )
    except (ValueError, LookupError) as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    await write_audit(
        db,
        actor_user_id=user.id,
        action=f"workflow.run_{body.decision}",
        resource_type="workflow_run",
        resource_id=run_uid,
        detail={"run_uid": run_uid},
    )
    nodes = await workflow_service.list_node_runs(db, run.id)
    payload = _run_to_wire(run, await _workflow_name(db, run.workflow_id))
    payload["nodes"] = [workflow_service.node_run_to_wire(n) for n in nodes]
    payload["definition"] = parse_definition(run.definition_json).model_dump()
    return payload


@router.post("/workflows/{workflow_id}/nodes/{node_id}/run")
async def run_single_node(
    workflow_id: int,
    node_id: str,
    body: RunInput,
    db: AsyncSession = Depends(get_db),
    user: AuthUser = Depends(require_auth),
) -> dict:
    """试跑单个节点：从开始节点真实执行到该节点为止，返回这一步的结果。"""

    row = await _get_row(db, workflow_id)
    try:
        definition = parse_definition(row.definition_json)
    except Exception as exc:
        raise HTTPException(status_code=400, detail=f"工作流定义无效: {exc}") from exc
    try:
        result = await workflow_service.run_single_node(
            db,
            workflow=row,
            definition=definition,
            node_id=node_id,
            payload=body.values,
            triggered_by=user.email,
        )
    except (ValueError, LookupError) as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    logger.info(
        "节点试跑 workflow=%s node=%s status=%s", workflow_id, node_id, result["status"]
    )
    return result


@router.get("/workflows/{workflow_id}")
async def get_workflow(
    workflow_id: int,
    db: AsyncSession = Depends(get_db),
    _user: str = Depends(require_auth),
) -> dict:
    row = await _get_row(db, workflow_id)
    return _row_to_wire(row, include_definition=True, last_run=await _latest_run(db, row.id))


@router.put("/workflows/{workflow_id}")
async def update_workflow(
    workflow_id: int,
    body: WorkflowInput,
    db: AsyncSession = Depends(get_db),
    user: AuthUser = Depends(require_auth),
) -> dict:
    """保存画布草稿：不动发布版本，也不改发布状态。"""

    row = await _get_row(db, workflow_id)
    duplicate = await db.scalar(
        select(WorkflowRow).where(
            WorkflowRow.name == body.name, WorkflowRow.id != workflow_id
        )
    )
    if duplicate is not None:
        raise HTTPException(status_code=400, detail=f"工作流名称已存在: {body.name}")
    row.name = body.name
    row.description = body.description
    row.definition_json = body.definition.model_dump_json()
    # 画布改动一律是草稿：已发布的工作流改过之后需要重新发布才算发布态。
    row.status = "draft"
    await db.commit()
    await db.refresh(row)
    await write_audit(
        db,
        actor_user_id=user.id,
        action="workflow.update",
        resource_type="workflow",
        resource_id=str(row.id),
        detail={"name": row.name, "version": row.version},
    )
    logger.info(
        "保存工作流草稿 id=%s version=%s nodes=%s",
        row.id,
        row.version,
        len(body.definition.nodes),
    )
    return _row_to_wire(row, include_definition=True, last_run=await _latest_run(db, row.id))


@router.post("/workflows/{workflow_id}/publish")
async def publish_workflow(
    workflow_id: int,
    db: AsyncSession = Depends(get_db),
    user: AuthUser = Depends(require_auth),
) -> dict:
    """发布当前草稿：校验通过才升版本，并把状态切成已发布。"""

    from app.adk_runtime.workflow_graph import validate

    row = await _get_row(db, workflow_id)
    try:
        definition = parse_definition(row.definition_json)
    except Exception as exc:
        raise HTTPException(status_code=400, detail=f"工作流定义无效: {exc}") from exc
    try:
        validate(definition)
    except Exception as exc:
        # 校验失败也落库：列表页能看到「最近一次发布为什么没过」。
        row.validation_error = str(exc)
        await db.commit()
        logger.info("发布校验不通过 id=%s: %s", workflow_id, exc)
        raise HTTPException(status_code=400, detail=f"工作流校验不通过: {exc}") from exc

    # 首次发布沿用初始版本号，之后每发布一次升一版。
    if row.published_at is not None:
        row.version += 1
    row.status = "published"
    row.published_at = datetime.now(timezone.utc)
    row.validation_error = ""
    await db.commit()
    await db.refresh(row)
    await write_audit(
        db,
        actor_user_id=user.id,
        action="workflow.publish",
        resource_type="workflow",
        resource_id=str(row.id),
        detail={"name": row.name, "version": row.version},
    )
    logger.info("发布工作流 id=%s version=%s", row.id, row.version)
    return _row_to_wire(row, include_definition=True, last_run=await _latest_run(db, row.id))


@router.delete("/workflows/{workflow_id}")
async def delete_workflow(
    workflow_id: int,
    db: AsyncSession = Depends(get_db),
    user: AuthUser = Depends(require_auth),
) -> dict:
    row = await _get_row(db, workflow_id)
    name = row.name
    await db.delete(row)
    await db.commit()
    await write_audit(
        db,
        actor_user_id=user.id,
        action="workflow.delete",
        resource_type="workflow",
        resource_id=str(workflow_id),
        detail={"name": name},
    )
    logger.info("删除工作流 id=%s name=%s", workflow_id, name)
    return {"deleted": workflow_id}


@router.post("/workflows/{workflow_id}/runs")
async def run_workflow(
    workflow_id: int,
    body: RunInput,
    db: AsyncSession = Depends(get_db),
    user: AuthUser = Depends(require_auth),
) -> dict:
    row = await _get_row(db, workflow_id)
    try:
        parse_definition(row.definition_json)
    except Exception as exc:
        raise HTTPException(status_code=400, detail=f"工作流定义无效: {exc}") from exc
    try:
        run = await workflow_service.execute_run(
            db,
            workflow=row,
            payload=body.values,
            triggered_by=user.email,
        )
    except (ValueError, LookupError) as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return _run_to_wire(run, row.name)
