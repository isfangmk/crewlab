"""技能声明 MCP 工具：目录出参、参数映射、运行时白名单合并。

这些用例只碰纯函数，不需要起后端：MCP 目录来自探测缓存（McpToolRef），
不连远程 Server。
"""

from __future__ import annotations

from google.adk.skills import Frontmatter, Skill

from app.services.agent_service import ResolvedAgent, effective_allowed_tools
from app.services.mcp_service import McpToolRef, mcp_tool_wire_name, skill_params_from_schema
from app.services.skills_service import SkillEntry, declared_tools_of_skills, skill_to_wire

_WIRE = mcp_tool_wire_name("ai-engine", "get_host_load")


def _skill(tools: list[str]) -> Skill:
    return Skill(
        frontmatter=Frontmatter(
            name="host-load",
            description="用 MCP 工具查主机负载",
            metadata={
                "risk": "safe",
                "category": "运维",
                "adk_additional_tools": tools,
            },
        ),
        instructions="# 步骤\n\n1. 调用查询工具\n2. 汇总结果",
    )


def _ref(*, available: bool = True) -> McpToolRef:
    return McpToolRef(
        server_name="ai-engine",
        tool_name="get_host_load",
        wire_name=_WIRE,
        description="查询主机负载",
        input_schema={
            "type": "object",
            "properties": {
                "host": {"type": "string", "description": "主机名"},
                "minutes": {"type": "integer", "default": 5},
                "verbose": {"type": ["boolean", "null"]},
            },
            "required": ["host"],
        },
        available=available,
    )


def test_wire_exposes_mcp_tool_with_schema_params() -> None:
    entry = SkillEntry(skill=_skill([_WIRE]), source="user")
    out = skill_to_wire(entry, mcp_tools={_WIRE: _ref()})

    assert out["tools"] == [_WIRE]
    # Server 可用 -> 技能可装配，不再被当成「目录里没有的工具」
    assert out["inventory_only"] is False
    assert out["tool_refs"] == [
        {
            "name": _WIRE,
            "kind": "mcp",
            "server": "ai-engine",
            "available": True,
            "description": "查询主机负载",
        }
    ]
    # 参数来自 input_schema，类型映射与内置工具目录一致
    assert out["params"] == [
        {"name": "host", "type": "string", "required": True, "desc": "主机名"},
        {"name": "minutes", "type": "int", "required": False, "default": 5},
        {"name": "verbose", "type": "bool", "required": False},
    ]


def test_disabled_server_marks_skill_inventory_only() -> None:
    entry = SkillEntry(skill=_skill([_WIRE]), source="user")
    out = skill_to_wire(entry, mcp_tools={_WIRE: _ref(available=False)})

    assert out["inventory_only"] is True
    assert out["tool_refs"][0]["kind"] == "mcp"
    assert out["tool_refs"][0]["available"] is False


def test_unknown_mcp_tool_is_flagged() -> None:
    entry = SkillEntry(skill=_skill([_WIRE]), source="user")
    out = skill_to_wire(entry, mcp_tools={})

    assert out["inventory_only"] is True
    assert out["tool_refs"] == [
        {"name": _WIRE, "kind": "unknown", "available": False, "description": ""}
    ]
    assert out["params"] == []


def test_schema_params_handle_empty_schema() -> None:
    assert skill_params_from_schema({"type": "object"}) == []


def test_declared_tools_of_skills_dedupes() -> None:
    skills = [_skill([_WIRE]), _skill([_WIRE, "query_knowledge"])]
    assert declared_tools_of_skills(skills) == {_WIRE, "query_knowledge"}


def _agent(tools: list[str]) -> ResolvedAgent:
    return ResolvedAgent(
        name="ops",
        description="运维助理",
        when_to_use="查负载",
        system_prompt="",
        critical_reminder="",
        tools=tools,
        disallowed_tools=[],
        permission_mode="",
        model="",
        max_turns=0,
        source="user",
    )


def test_effective_allowed_tools_merges_skill_declared_mcp() -> None:
    allowed = effective_allowed_tools(_agent(["query_knowledge"]), [_skill([_WIRE])])
    # 技能后加声明也要补齐：否则 Server 不会被挂载
    assert allowed == ["query_knowledge", _WIRE]


def test_effective_allowed_tools_keeps_inherit_all() -> None:
    # 空白名单 = 继承全部，不展开成显式列表
    assert effective_allowed_tools(_agent([]), [_skill([_WIRE])]) == []
