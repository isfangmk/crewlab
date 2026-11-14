"""MCP 工具参数识别：schema 转表单定义与执行前类型转换。

画布参数一律以文本录入，这里覆盖标量/JSON 类型转换、必填检查与 schema 读取，
不连接真实 MCP Server。
"""

from __future__ import annotations

import json

import pytest

from app.models_orm.mcp import McpServer
from app.services.mcp_service import (
    coerce_arguments_from_schema,
    skill_params_from_schema,
    tool_input_schema,
)

_ADD_SCHEMA = {
    "type": "object",
    "properties": {
        "a": {"type": "integer", "title": "A"},
        "b": {"type": "integer", "title": "B"},
    },
    "required": ["a", "b"],
}


def test_skill_params_from_schema_exposes_required_and_types() -> None:
    specs = {spec["name"]: spec for spec in skill_params_from_schema(_ADD_SCHEMA)}

    assert specs["a"]["type"] == "int"
    assert specs["a"]["required"] is True
    assert specs["b"]["type"] == "int"
    # 技能目录保持原契约：原始 JSON Schema 类型只在画布参数里出现
    assert "schema_type" not in specs["a"]

    canvas_specs = {
        spec["name"]: spec
        for spec in skill_params_from_schema(_ADD_SCHEMA, include_schema_type=True)
    }
    assert canvas_specs["a"]["schema_type"] == "integer"


def test_coerce_arguments_converts_text_to_schema_types() -> None:
    assert coerce_arguments_from_schema(_ADD_SCHEMA, {"a": "2", "b": "3"}) == {
        "a": 2,
        "b": 3,
    }

    schema = {
        "type": "object",
        "properties": {
            "ratio": {"type": "number"},
            "enabled": {"type": "boolean"},
            "tags": {"type": "array"},
            "options": {"type": "object"},
            "label": {"type": "string"},
        },
    }
    args = coerce_arguments_from_schema(
        schema,
        {
            "ratio": "0.5",
            "enabled": "true",
            "tags": '["a", "b"]',
            "options": '{"level": 1}',
            "label": 5,
        },
    )
    assert args == {
        "ratio": 0.5,
        "enabled": True,
        "tags": ["a", "b"],
        "options": {"level": 1},
        "label": "5",
    }


def test_coerce_arguments_rejects_missing_and_invalid() -> None:
    with pytest.raises(ValueError, match="缺少必填参数"):
        coerce_arguments_from_schema(_ADD_SCHEMA, {"a": "2"})
    with pytest.raises(ValueError, match="需要整数"):
        coerce_arguments_from_schema(_ADD_SCHEMA, {"a": "x", "b": "3"})
    with pytest.raises(ValueError, match="合法 JSON"):
        coerce_arguments_from_schema(
            {"type": "object", "properties": {"tags": {"type": "array"}}},
            {"tags": "not-json"},
        )


def test_coerce_arguments_keeps_unknown_keys() -> None:
    """schema 之外的参数不猜类型，交给 MCP Server 自己校验。"""

    assert coerce_arguments_from_schema(
        {"type": "object", "properties": {"a": {"type": "integer"}}},
        {"a": "1", "extra": "x"},
    ) == {"a": 1, "extra": "x"}


def test_tool_input_schema_reads_cache() -> None:
    server = McpServer(
        name="demo",
        transport="http",
        endpoint="http://127.0.0.1:1/mcp",
        tools_cache_json=json.dumps(
            [
                {
                    "name": "add",
                    "description": "两数相加",
                    "input_schema": _ADD_SCHEMA,
                }
            ],
            ensure_ascii=False,
        ),
    )

    assert tool_input_schema(server, "add") == _ADD_SCHEMA
    assert tool_input_schema(server, "missing") is None
