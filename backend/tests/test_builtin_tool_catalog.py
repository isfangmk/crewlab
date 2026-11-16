"""画布工具节点的内置工具目录与参数转换。

画布参数一律以文本录入，执行前必须按工具签名转成真实类型；
这里覆盖类型转换、必填/未知参数与目录过滤规则，不触达外部服务。
"""

from __future__ import annotations

import pytest

from app.services.platform.builtin_tool_service import list_builtin_tool_groups
from app.tools.catalog import (
    BUILTIN_TOOL_SPECS,
    WORKFLOW_NODE_TOOL_NAMES,
    _coerce_value,
    coerce_tool_params,
    workflow_builtin_tools,
)


def test_every_tool_group_has_title() -> None:
    """每个工具分组必须登记标题：缺标题会让 /tools/builtin 目录接口直接 500。"""

    groups = list_builtin_tool_groups()
    assert [g["key"] for g in groups] == list(
        dict.fromkeys(spec["group"] for spec in BUILTIN_TOOL_SPECS)
    )
    assert all(g["title"] != "" and g["title_en"] != "" for g in groups)


def test_workflow_builtin_tools_exposes_params() -> None:
    """目录项自带参数定义，前端按签名渲染表单。"""

    tools = {tool["name"]: tool for tool in workflow_builtin_tools()}

    assert set(tools) == WORKFLOW_NODE_TOOL_NAMES
    workflow = tools["list_workflows"]
    assert workflow["group"] == "workflow"
    assert workflow["group_title"] == "工作流"
    assert workflow["group_title_en"] == "Workflow"
    params = {param["name"]: param for param in workflow["params"]}
    assert params["status"]["required"] is False
    assert params["status"]["type"] == "string"
    assert params["limit"]["type"] == "int"
    assert params["limit"]["default"] == 20


def test_workflow_builtin_tools_only_exposes_necessary_tools() -> None:
    """画布只给没有专用节点的工作流查询工具。"""

    names = {tool["name"] for tool in workflow_builtin_tools()}

    # 需要人工确认、已有专用节点、依赖会话上下文的工具都不进画布
    assert "run_workflow" not in names
    assert "query_knowledge" not in names
    assert "web_search" not in names
    assert "run_python" not in names
    assert "remember_memory" not in names
    assert "recall_memory" not in names
    assert "list_workflows" in names
    assert "get_workflow" in names
    assert "list_workflow_runs" in names
    assert "get_workflow_run" in names


def test_workflow_tool_allowlist_is_safe() -> None:
    """白名单里的名字必须存在，且都是 safe——画布无人值守只跑无副作用工具。"""

    risks = {spec["name"]: spec["risk"] for spec in BUILTIN_TOOL_SPECS}
    assert WORKFLOW_NODE_TOOL_NAMES <= set(risks)
    assert all(risks[name] == "safe" for name in WORKFLOW_NODE_TOOL_NAMES)


def test_coerce_tool_params_converts_text() -> None:
    assert coerce_tool_params(
        "query_knowledge", {"query": "DNS", "limit": "8"}
    ) == {"query": "DNS", "limit": 8}


def test_coerce_tool_params_empty_optional_uses_default() -> None:
    """空字符串视为未填，交给签名默认值。"""

    assert coerce_tool_params("query_knowledge", {"query": "DNS", "limit": ""}) == {
        "query": "DNS"
    }


def test_coerce_tool_params_rejects_bad_input() -> None:
    with pytest.raises(ValueError, match="缺少必填参数"):
        coerce_tool_params("query_knowledge", {})
    with pytest.raises(ValueError, match="不接受参数"):
        coerce_tool_params("query_knowledge", {"query": "DNS", "unknown": "1"})
    with pytest.raises(ValueError, match="需要整数"):
        coerce_tool_params("query_knowledge", {"query": "DNS", "limit": "1.5"})


def test_coerce_value_types() -> None:
    assert _coerce_value("t", "p", "int", "42") == 42
    assert _coerce_value("t", "p", "int", "-3") == -3
    assert _coerce_value("t", "p", "int", 7.0) == 7
    assert _coerce_value("t", "p", "float", "2.5") == 2.5
    assert _coerce_value("t", "p", "float", "1e3") == 1000.0
    assert _coerce_value("t", "p", "bool", "true") is True
    assert _coerce_value("t", "p", "bool", "FALSE") is False
    assert _coerce_value("t", "p", "bool", "1") is True
    assert _coerce_value("t", "p", "string", 5) == "5"

    with pytest.raises(ValueError, match="布尔值"):
        _coerce_value("t", "p", "int", True)
    with pytest.raises(ValueError, match="需要整数"):
        _coerce_value("t", "p", "int", "abc")
    with pytest.raises(ValueError, match="需要 true/false"):
        _coerce_value("t", "p", "bool", "maybe")
    with pytest.raises(ValueError, match="文本类型"):
        _coerce_value("t", "p", "string", ["a"])
