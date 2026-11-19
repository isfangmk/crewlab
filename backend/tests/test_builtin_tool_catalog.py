"""内置工具目录的参数转换。

画布参数一律以文本录入，执行前必须按工具签名转成真实类型；
这里覆盖类型转换、必填/未知参数与目录登记规则，不触达外部服务。
"""

from __future__ import annotations

import pytest

from app.services.platform.builtin_tool_service import list_builtin_tool_groups
from app.tools.catalog import (
    BUILTIN_TOOL_SPECS,
    _coerce_value,
    builtin_tool_registry,
    coerce_tool_params,
    tool_param_specs,
)


def test_memory_update_delete_tools_registered_as_mutating() -> None:
    """记忆改写/删除工具：归入 memory 组、走审批（mutating）、参数可反射。"""

    specs = {spec["name"]: spec for spec in BUILTIN_TOOL_SPECS}
    for name in ("update_memory", "delete_memory"):
        assert specs[name]["group"] == "memory"
        assert specs[name]["risk"] == "mutating"
        assert specs[name]["mount"] == "always"
        assert name in builtin_tool_registry()

    update_params = {p["name"]: p for p in tool_param_specs(builtin_tool_registry()["update_memory"])}
    assert update_params["memory_id"]["required"] is True
    assert update_params["content"]["required"] is True
    delete_params = {p["name"]: p for p in tool_param_specs(builtin_tool_registry()["delete_memory"])}
    assert delete_params["memory_id"]["required"] is True
    # tool_context 是内部注入参数，不对模型暴露
    assert "tool_context" not in update_params
    assert "tool_context" not in delete_params


def test_every_tool_group_has_title() -> None:
    """每个工具分组必须登记标题：缺标题会让 /tools/builtin 目录接口直接 500。"""

    groups = list_builtin_tool_groups()
    assert [g["key"] for g in groups] == list(
        dict.fromkeys(spec["group"] for spec in BUILTIN_TOOL_SPECS)
    )
    assert all(g["title"] != "" and g["title_en"] != "" for g in groups)


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
