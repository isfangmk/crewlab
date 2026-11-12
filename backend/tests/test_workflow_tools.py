"""工作流内置工具。

覆盖范围：
  - 目录声明：分组按能力主题划分，工作流组覆盖列表 / 定义 / 运行 / 记录 / 结果；
  - 参数导出：tool_param_specs 按函数签名给出必填与类型，前端表单据此渲染；
  - 注册表：Skill 声明的 additional_tools 必须能解析到具体函数；
  - 查询行为：走真实数据库（把 SessionLocal 换成用例自己的 sessionmaker），
    验证筛选、空结果与不存在时的显式报错。
不覆盖：run_workflow 的真实执行（会调模型与外部服务，由 API 层集成测试兜住）。
"""

from __future__ import annotations

import uuid
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager
from unittest.mock import patch

import pytest

from app.tools import workflow
from app.tools.catalog import (
    BUILTIN_TOOL_SPECS,
    builtin_tool_registry,
    tool_param_specs,
)


@asynccontextmanager
async def _db_sessions() -> AsyncGenerator[object, None]:
    """用例独立的 sessionmaker。

    模块级 engine 会跨事件循环复用连接池，而每个 async 用例跑在自己的循环里，
    直接用它会在下一次用例里报 Event loop is closed。
    """

    from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

    from app.config import get_settings

    engine = create_async_engine(get_settings().database_url)
    try:
        yield async_sessionmaker(engine, expire_on_commit=False)
    finally:
        await engine.dispose()


async def _create_workflow(definition: dict, name: str) -> int:
    """插入一张真实工作流，返回自增 ID。"""

    from app.models_orm.workflow import Workflow as WorkflowRow
    from app.schemas.workflow import WorkflowDefinition

    parsed = WorkflowDefinition.model_validate(definition)
    async with _db_sessions() as maker:
        async with maker() as db:
            row = WorkflowRow(
                name=name,
                description="内置工具测试用画布",
                definition_json=parsed.model_dump_json(),
                version=1,
                status="draft",
            )
            db.add(row)
            await db.commit()
            await db.refresh(row)
            return row.id


async def _delete_workflow(workflow_id: int) -> None:
    from sqlalchemy import delete

    from app.models_orm.workflow import Workflow as WorkflowRow

    async with _db_sessions() as maker:
        async with maker() as db:
            await db.execute(delete(WorkflowRow).where(WorkflowRow.id == workflow_id))
            await db.commit()


def _simple_flow() -> dict:
    return {
        "nodes": [
            {"id": "start", "kind": "start", "variables": [{"name": "topic", "required": True}]},
            {"id": "prep", "kind": "assign", "assignments": {"q": "关于 {{topic}} 的资料"}},
            {"id": "end", "kind": "end", "output_key": "q"},
        ],
        "edges": [
            {"id": "e1", "source": "start", "target": "prep"},
            {"id": "e2", "source": "prep", "target": "end"},
        ],
    }


# ---------------- 目录与分组 ----------------


def test_groups_use_subject_keys_not_catch_all() -> None:
    """分组要说明「这是哪一类能力」，不能用「内置」这种同义反复的 key。"""

    groups = {spec["group"] for spec in BUILTIN_TOOL_SPECS}
    assert "builtin" not in groups
    assert groups == {"knowledge", "search", "workflow"}


def test_workflow_tools_cover_listing_detail_running_and_results() -> None:
    """工作流要能列表、看定义、跑一次、查运行记录、查单次结果。"""

    names = [spec["name"] for spec in BUILTIN_TOOL_SPECS if spec["group"] == "workflow"]
    assert names == [
        "list_workflows",
        "get_workflow",
        "run_workflow",
        "list_workflow_runs",
        "get_workflow_run",
    ]


def test_only_run_workflow_requires_approval() -> None:
    """只有会真实执行画布的工具需要审批，其余是只读查询。"""

    risky = [spec["name"] for spec in BUILTIN_TOOL_SPECS if spec["risk"] != "safe"]
    assert risky == ["run_workflow"]
    spec = next(s for s in BUILTIN_TOOL_SPECS if s["name"] == "run_workflow")
    assert spec["risk"] == "approval_required"


# ---------------- 参数导出 ----------------


def test_param_specs_mark_required_and_types() -> None:
    specs = {p["name"]: p for p in tool_param_specs(workflow.get_workflow)}
    assert specs["workflow_id"]["required"] is True
    assert specs["workflow_id"]["type"] == "int"

    run_specs = {p["name"]: p for p in tool_param_specs(workflow.run_workflow)}
    assert run_specs["workflow_id"]["required"] is True
    # values 有默认值，属于可选
    assert run_specs["values"]["required"] is False

    run_detail = {p["name"]: p for p in tool_param_specs(workflow.get_workflow_run)}
    assert run_detail["run_uid"]["required"] is True
    assert run_detail["max_chars"]["type"] == "int"
    assert run_detail["max_chars"]["default"] == 2000


def test_param_specs_keep_docstring_descriptions() -> None:
    specs = {p["name"]: p for p in tool_param_specs(workflow.list_workflows)}
    assert specs["keyword"]["desc"] == "按名称或描述模糊匹配，留空表示不限"


# ---------------- 注册表 ----------------


def test_registry_resolves_every_declared_tool_except_per_turn_web_search() -> None:
    """目录里声明的工具都要能按名字解析；web_search 走对话开关，不在注册表。"""

    registry = builtin_tool_registry()
    declared = {spec["name"] for spec in BUILTIN_TOOL_SPECS}
    missing = declared - set(registry) - {"web_search"}
    assert missing == set()


# ---------------- 查询行为 ----------------


@pytest.mark.asyncio
async def test_list_workflows_returns_inputs_and_latest_run() -> None:
    name = f"工具测试画布-{uuid.uuid4().hex[:6]}"
    workflow_id = await _create_workflow(_simple_flow(), name)
    try:
        async with _db_sessions() as maker:
            with patch.object(workflow, "SessionLocal", maker):
                result = await workflow.list_workflows(keyword=name)
        assert result["total"] == 1
        item = result["workflows"][0]
        assert item["id"] == workflow_id
        assert item["node_count"] == 3
        assert item["edge_count"] == 2
        # 运行输入来自开始节点声明，必填项要标出来
        assert item["inputs"] == [{"name": "topic", "type": "string", "required": True}]
        assert item["last_run"] is None
    finally:
        await _delete_workflow(workflow_id)


@pytest.mark.asyncio
async def test_list_workflows_rejects_out_of_range_limit() -> None:
    with pytest.raises(ValueError, match="limit 必须在 1-50 之间"):
        await workflow.list_workflows(limit=999)


@pytest.mark.asyncio
async def test_get_workflow_returns_nodes_edges_and_inputs() -> None:
    name = f"工具测试画布-{uuid.uuid4().hex[:6]}"
    workflow_id = await _create_workflow(_simple_flow(), name)
    try:
        async with _db_sessions() as maker:
            with patch.object(workflow, "SessionLocal", maker):
                detail = await workflow.get_workflow(workflow_id)
        assert [n["id"] for n in detail["nodes"]] == ["start", "prep", "end"]
        assert [n["kind"] for n in detail["nodes"]] == ["start", "assign", "end"]
        assert detail["edges"] == [
            {"source": "start", "target": "prep", "route": ""},
            {"source": "prep", "target": "end", "route": ""},
        ]
        assert [i["name"] for i in detail["inputs"]] == ["topic"]
    finally:
        await _delete_workflow(workflow_id)


@pytest.mark.asyncio
async def test_get_workflow_raises_for_missing_id() -> None:
    async with _db_sessions() as maker:
        with patch.object(workflow, "SessionLocal", maker):
            with pytest.raises(LookupError, match="工作流不存在"):
                await workflow.get_workflow(999_999_999)


@pytest.mark.asyncio
async def test_get_workflow_run_raises_for_missing_uid() -> None:
    async with _db_sessions() as maker:
        with patch.object(workflow, "SessionLocal", maker):
            with pytest.raises(LookupError, match="运行记录不存在"):
                await workflow.get_workflow_run("run_not_a_real_uid")


@pytest.mark.asyncio
async def test_list_workflow_runs_filters_by_status() -> None:
    async with _db_sessions() as maker:
        with patch.object(workflow, "SessionLocal", maker):
            result = await workflow.list_workflow_runs(status="succeeded", limit=5)
    assert all(run["status"] == "succeeded" for run in result["runs"])
    # total 是筛选后的总数，不受 limit 影响
    assert result["total"] >= len(result["runs"])


# ---------------- 输出截断 ----------------


def test_trim_keeps_short_text_and_marks_truncation() -> None:
    assert workflow._trim("short", 200) == "short"

    long_text = "x" * 500
    trimmed = workflow._trim(long_text, 200)
    assert trimmed.startswith("x" * 200)
    assert "已截断" in trimmed
    assert "500" in trimmed


def test_trim_keeps_empty_text_empty() -> None:
    assert workflow._trim("", 200) == ""
