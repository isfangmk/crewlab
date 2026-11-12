"""工作流内置工具：看画布、跑工作流、查运行结果。

工具的行为住在 app/tools 下，adk_runtime 只做目录声明与装配，
两边职责分开，运行时目录不会变成工具实现的堆放处。
"""

from __future__ import annotations

import json
import logging
from typing import Any

from app.db import SessionLocal

logger = logging.getLogger(__name__)

# 工具触发的运行没有登录用户，但运行记录要能看出「不是人点的」
TRIGGERED_BY = "agent@builtin"


async def list_workflows(status: str = "", keyword: str = "", limit: int = 20) -> dict:
    """列出工作流，返回 ID、名称、节点与连线数量、状态和最近一次运行。

    先用它拿到工作流 ID，再按 ID 查定义或运行。
    状态：draft 草稿、published 已发布。

    Args:
        status: 按状态筛选，留空表示不限
        keyword: 按名称或描述模糊匹配，留空表示不限
        limit: 返回条数上限
    """

    from sqlalchemy import func, or_, select

    from app.models_orm.workflow import Workflow as WorkflowRow
    from app.models_orm.workflow import WorkflowRun as WorkflowRunRow
    from app.schemas.workflow import parse_definition

    if limit < 1 or limit > 50:
        raise ValueError(f"limit 必须在 1-50 之间: {limit}")

    async with SessionLocal() as db:
        conditions: list[Any] = []
        if status != "":
            conditions.append(WorkflowRow.status == status)
        if keyword != "":
            conditions.append(
                or_(
                    WorkflowRow.name.ilike(f"%{keyword}%"),
                    WorkflowRow.description.ilike(f"%{keyword}%"),
                )
            )
        stmt = select(WorkflowRow)
        count_stmt = select(func.count()).select_from(WorkflowRow)
        for condition in conditions:
            stmt = stmt.where(condition)
            count_stmt = count_stmt.where(condition)
        rows = list(
            (await db.scalars(stmt.order_by(WorkflowRow.id.desc()).limit(limit))).all()
        )
        total = int(await db.scalar(count_stmt) or 0)

        latest = await _latest_runs(db, {row.id for row in rows})
        items = []
        for row in rows:
            definition = parse_definition(row.definition_json)
            run = latest.get(row.id)
            items.append(
                {
                    "id": row.id,
                    "name": row.name,
                    "description": row.description,
                    "status": row.status,
                    "version": row.version,
                    "node_count": len(definition.nodes),
                    "edge_count": len(definition.edges),
                    # 没发布过就没有可运行的版本，run_workflow 会直接报错
                    "has_published": row.published_definition_json != "",
                    # 运行输入来自开始节点的变量声明，跑之前得知道要传什么
                    "inputs": [
                        {"name": var.name, "type": var.type, "required": var.required}
                        for var in definition.require_start().variables
                    ],
                    "last_run": None
                    if run is None
                    else {
                        "run_uid": run.run_uid,
                        "status": run.status,
                        "created_at": run.created_at.isoformat(),
                    },
                }
            )
    return {"total": total, "returned": len(items), "workflows": items}


async def get_workflow(workflow_id: int) -> dict:
    """查单个工作流的完整定义：节点（含类型与关键参数）、连线、运行输入。

    用来理解一张画布怎么串起来的，或确认某次运行该传哪些值。

    Args:
        workflow_id: 工作流 ID（从 list_workflows 拿）
    """

    from app.models_orm.workflow import Workflow as WorkflowRow
    from app.schemas.workflow import parse_definition

    async with SessionLocal() as db:
        row = await db.get(WorkflowRow, workflow_id)
        if row is None:
            raise LookupError(f"工作流不存在: {workflow_id}")
        definition = parse_definition(row.definition_json)

    return {
        "id": row.id,
        "name": row.name,
        "description": row.description,
        "status": row.status,
        "version": row.version,
        "nodes": [
            {
                "id": node.id,
                "kind": node.kind,
                "label": node.label,
                "output_key": node.output_key,
                "mcp_server": node.mcp_server,
                "mcp_tool": node.mcp_tool,
                "provider": node.provider,
                "model": node.model,
            }
            for node in definition.nodes
        ],
        "edges": [
            {"source": edge.source, "target": edge.target, "route": edge.route}
            for edge in definition.edges
        ],
        "inputs": [
            {
                "name": var.name,
                "type": var.type,
                "required": var.required,
                "default": var.default,
                "description": var.description,
            }
            for var in definition.require_start().variables
        ],
    }


async def run_workflow(workflow_id: int, values: dict[str, Any] | None = None) -> dict:
    """运行一次工作流，返回运行流水号与最终状态。

    跑的是最近一次发布的版本，不是正在编辑的草稿；没发布过会直接报错。
    会真实执行画布上的每一步（可能调模型、查知识库、调 MCP 工具、发 HTTP 请求）。
    必填输入漏传会直接报错；状态 waiting 表示停在人工节点等人确认。

    Args:
        workflow_id: 工作流 ID（从 list_workflows 拿）
        values: 运行输入，键名见 list_workflows 返回的 inputs
    """

    from app.models_orm.workflow import Workflow as WorkflowRow
    from app.services import workflow_service

    async with SessionLocal() as db:
        row = await db.get(WorkflowRow, workflow_id)
        if row is None:
            raise LookupError(f"工作流不存在: {workflow_id}")
        run = await workflow_service.execute_run(
            db,
            workflow=row,
            payload=values or {},
            triggered_by=TRIGGERED_BY,
            source="published",
        )
    logger.info(
        "内置工具触发工作流 workflow=%s run_uid=%s status=%s",
        workflow_id,
        run.run_uid,
        run.status,
    )
    return {
        "run_uid": run.run_uid,
        "workflow_id": run.workflow_id,
        "status": run.status,
        "duration_ms": run.duration_ms,
        "error": run.error,
        "output": run.output_json,
    }


async def list_workflow_runs(
    status: str = "",
    keyword: str = "",
    limit: int = 10,
) -> dict:
    """查工作流运行记录，返回每次运行的流水号、所属工作流、状态、耗时与错误。

    状态取值：succeeded 成功、failed 失败、waiting 停在人工节点等确认、
    running 执行中。按时间倒序，最近的排在最前。
    用流水号可再查该次运行的逐节点结果，据此回答「哪一步出了问题」。

    Args:
        status: 按状态筛选，留空表示不限
        keyword: 按工作流名称、流水号或触发人模糊匹配，留空表示不限
        limit: 返回条数上限
    """

    from app.services import workflow_service

    if limit < 1 or limit > 50:
        raise ValueError(f"limit 必须在 1-50 之间: {limit}")

    async with SessionLocal() as db:
        rows, total = await workflow_service.list_runs(
            db,
            workflow_id=None,
            status=status,
            keyword=keyword,
            limit=limit,
            offset=0,
        )
        names = await _workflow_names(db, {row.workflow_id for row in rows})
        items = [
            {
                "run_uid": row.run_uid,
                "workflow": names.get(row.workflow_id, ""),
                "status": row.status,
                "version": row.workflow_version,
                "triggered_by": row.triggered_by,
                "duration_ms": row.duration_ms,
                "error": row.error,
                "created_at": row.created_at.isoformat(),
            }
            for row in rows
        ]
    return {"total": total, "returned": len(items), "runs": items}


async def get_workflow_run(run_uid: str, max_chars: int = 2000) -> dict:
    """查单次运行的完整结果：整体状态、耗时、报错，以及每一步的输入与输出。

    回答「这次跑到哪一步」「哪一步失败了」「这一步吃了什么、吐出什么」用它。
    详细正文可能很长，先用 max_chars 截断，需要原文再调大。

    Args:
        run_uid: 运行流水号（从 list_workflow_runs 或 run_workflow 拿）
        max_chars: 每个节点输入与输出各自的最大字符数
    """

    from app.services import workflow_service

    if max_chars < 200 or max_chars > 20000:
        raise ValueError(f"max_chars 必须在 200-20000 之间: {max_chars}")

    async with SessionLocal() as db:
        run = await workflow_service.get_run(db, run_uid)
        nodes = await workflow_service.list_node_runs(db, run.id)
        name = await _workflow_names(db, {run.workflow_id})

    return {
        "run_uid": run.run_uid,
        "workflow": name.get(run.workflow_id, ""),
        "workflow_id": run.workflow_id,
        "status": run.status,
        "version": run.workflow_version,
        "triggered_by": run.triggered_by,
        "duration_ms": run.duration_ms,
        "error": run.error,
        "output": run.output_json,
        "nodes": [
            {
                "node_id": row.node_id,
                "kind": row.node_kind,
                "attempt": row.attempt,
                "status": row.status,
                "duration_ms": row.duration_ms,
                "route": row.route,
                "error": row.error,
                "input": _trim(row.input_json, max_chars),
                "output": _trim(row.output_json, max_chars),
            }
            for row in nodes
        ],
    }


def _trim(text: str, max_chars: int) -> str:
    """长输出只给开头，避免整段模型正文塞满上下文；截断处显式标注。"""

    if text == "" or len(text) <= max_chars:
        return text
    return f"{text[:max_chars]}…（已截断，原文 {len(text)} 字符）"


async def _workflow_names(db: Any, ids: set[int]) -> dict[int, str]:
    """批量取工作流名称；运行记录里只存了 id，展示需要名字。"""

    from sqlalchemy import select

    from app.models_orm.workflow import Workflow as WorkflowRow

    if ids == set():
        return {}
    stmt = select(WorkflowRow.id, WorkflowRow.name).where(WorkflowRow.id.in_(ids))
    return {row_id: name for row_id, name in (await db.execute(stmt)).all()}


async def _latest_runs(db: Any, ids: set[int]) -> dict[int, Any]:
    """每个工作流最近一次运行：列表要展示运行状态，一次查完避免逐行查询。"""

    from sqlalchemy import func, select

    from app.models_orm.workflow import WorkflowRun as WorkflowRunRow

    if ids == set():
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


def parse_output(text: str) -> Any:
    """把落库的 JSON 文本还原成对象，供调用方直接使用；不是 JSON 就原样返回。"""

    if text == "":
        return None
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        return text
