"""画布工具节点的内置工具目录与参数转换。

画布参数一律以文本录入，执行前必须按工具签名转成真实类型；
这里覆盖类型转换、必填/未知参数与目录过滤规则，不触达外部服务。
"""

from __future__ import annotations

import pytest

from app.tools.catalog import (
    _coerce_value,
    coerce_tool_params,
    workflow_builtin_tools,
)


def test_workflow_builtin_tools_exposes_params() -> None:
    """目录项自带参数定义，前端按签名渲染表单。"""

    tools = {tool["name"]: tool for tool in workflow_builtin_tools()}

    knowledge = tools["query_knowledge"]
    assert knowledge["group"] == "knowledge"
    assert knowledge["group_title"] == "知识检索"
    assert knowledge["group_title_en"] == "Knowledge"
    params = {param["name"]: param for param in knowledge["params"]}
    assert params["query"]["required"] is True
    assert params["query"]["type"] == "string"
    assert params["limit"]["type"] == "int"
    assert params["limit"]["default"] == 5


def test_workflow_builtin_tools_skips_approval_tools() -> None:
    """无人值守的画布节点不能绕过人工确认闸门。"""

    names = {tool["name"] for tool in workflow_builtin_tools()}

    assert "run_workflow" not in names
    assert "list_workflows" in names


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
