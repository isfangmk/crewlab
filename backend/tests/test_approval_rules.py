"""审批规则引擎：匹配优先级、名单改写、闸门压过会话信任、升级审批与 SLA 提醒。"""

from __future__ import annotations

import json
import uuid
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker

from app.adk_runtime.stream_bridge import stream_adk_confirmation, stream_adk_turn
from app.adk_runtime.tool_approval import (
    _PENDING_CONFIRMATION_ERROR,
    _RULE_BLOCKED_ERROR,
    SESSION_TRUST_KEY,
    build_before_tool_callback,
)
from app.db import create_db_engine
from app.models.approval_rule import ApprovalRule
from app.models.base import Base
from app.models.chat import ChatSession
from app.models.llm import AppSetting
from app.services.chat import approval_rule_service, approval_service
from app.services.chat.approval_rule_service import (
    apply_rule_actions,
    pick_rule,
    validate_rule_fields,
)


def _new_sessionmaker(db_path):
    engine = create_db_engine(f"sqlite+aiosqlite:///{db_path.as_posix()}")
    return engine, async_sessionmaker(engine, expire_on_commit=False)


def _rule(
    pattern: str,
    action: str,
    *,
    rule_id: str = "",
    name: str = "r",
    agent_id: str | None = None,
    risk: str | None = None,
    priority: int = 0,
    enabled: bool = True,
    created_at: datetime | None = None,
) -> ApprovalRule:
    return ApprovalRule(
        id=rule_id or f"rule_{uuid.uuid4().hex[:8]}",
        name=name,
        enabled=enabled,
        tool_pattern=pattern,
        agent_id=agent_id,
        risk=risk,
        action=action,
        priority=priority,
        created_at=created_at or datetime(2026, 1, 1, tzinfo=timezone.utc),
    )


def test_pick_rule_precedence() -> None:
    """首条命中生效：无通配 > 前缀通配 > 通配；助理专属 > 全局；priority 大者先。"""

    rules = [
        _rule("*", "auto_approve", name="catch-all"),
        _rule("mcp__obs-tools__*", "require_approval", name="server-prefix"),
        _rule("mcp__obs-tools__acknowledge_alert", "escalate", name="exact"),
        _rule("mcp__obs-tools__blocked", "block", name="global-block"),
        _rule("mcp__obs-tools__blocked", "escalate", name="agent-block", agent_id="ops", priority=9),
    ]
    # 具体名压过前缀通配与通配
    d = pick_rule(rules, tool_name="mcp__obs-tools__acknowledge_alert", agent_id=None, risk="safe")
    assert d is not None and d.rule_name == "exact"
    # 前缀通配压过通配
    d = pick_rule(rules, tool_name="mcp__obs-tools__query_metric", agent_id=None, risk="safe")
    assert d is not None and d.rule_name == "server-prefix"
    # 通配兜底
    d = pick_rule(rules, tool_name="run_python", agent_id=None, risk="safe")
    assert d is not None and d.rule_name == "catch-all"
    # 助理专属压过全局（同为无通配）
    d = pick_rule(rules, tool_name="mcp__obs-tools__blocked", agent_id="ops", risk="dangerous")
    assert d is not None and d.rule_name == "agent-block"
    d = pick_rule(rules, tool_name="mcp__obs-tools__blocked", agent_id="other", risk="dangerous")
    assert d is not None and d.rule_name == "global-block"

    # 风险过滤：risk=dangerous 的规则不命中 safe 工具
    d = pick_rule(
        [_rule("tool-x", "escalate", risk="dangerous")],
        tool_name="tool-x",
        agent_id=None,
        risk="safe",
    )
    assert d is None
    # 停用规则不参与
    d = pick_rule(
        [_rule("tool-x", "block", enabled=False)],
        tool_name="tool-x",
        agent_id=None,
        risk=None,
    )
    assert d is None


def test_apply_rule_actions_rewrites_names() -> None:
    """auto_approve 强出审批名单；require/escalate 强进；block 进禁用名单。"""

    confirm, disallowed = apply_rule_actions(
        {"tool-a", "tool-b"},
        {"tool-c"},
        {
            "tool-a": "auto_approve",
            "tool-b": "escalate",
            "tool-c": "require_approval",
            "tool-d": "block",
        },
    )
    assert confirm == {"tool-b", "tool-c"}
    assert disallowed == {"tool-d"}


def test_validate_rule_fields() -> None:
    validate_rule_fields(name="n", tool_pattern="t", risk=None, action="block")
    validate_rule_fields(name="n", tool_pattern="t", risk="mutating", action="block")
    validate_rule_fields(name="n", tool_pattern="t", risk="dangerous", action="block")
    with pytest.raises(ValueError):
        validate_rule_fields(name=" ", tool_pattern="t", risk=None, action="block")
    with pytest.raises(ValueError):
        validate_rule_fields(name="n", tool_pattern="t", risk="high", action="block")
    with pytest.raises(ValueError):
        validate_rule_fields(name="n", tool_pattern="t", risk=None, action="skip")


class _FakeTool:
    def __init__(self, name: str) -> None:
        self.name = name


def _fake_tool_context(confirmation=None, state=None):
    requested: list[str] = []

    def request_confirmation(hint: str = "") -> None:
        requested.append(hint)

    return (
        SimpleNamespace(
            tool_confirmation=confirmation,
            actions=SimpleNamespace(skip_summarization=False),
            function_call_id="call-1",
            request_confirmation=request_confirmation,
            state={} if state is None else state,
            session=SimpleNamespace(id="session-1"),
        ),
        requested,
    )


@pytest.mark.asyncio
async def test_gate_rule_overrides_session_trust() -> None:
    """规则 require/escalate 不被会话信任豁免；auto_approve 直接放行；block 拒绝。"""

    callback = build_before_tool_callback(
        {"tool-req", "tool-auto"},
        set(),
        {"tool-req": "require_approval", "tool-auto": "auto_approve", "tool-block": "block"},
    )

    # 已置全量免审，但规则强制进闸：仍要确认
    ctx, requested = _fake_tool_context(None, state={SESSION_TRUST_KEY: "all"})
    result = await callback(_FakeTool("tool-req"), {}, ctx)
    assert result == {"error": _PENDING_CONFIRMATION_ERROR}
    assert len(requested) == 1

    # auto_approve：即使在确认名单也不拦
    ctx, requested = _fake_tool_context(None, state={SESSION_TRUST_KEY: "all"})
    assert await callback(_FakeTool("tool-auto"), {}, ctx) is None
    assert requested == []

    # block：直接拒绝，不进审批
    ctx, requested = _fake_tool_context(None)
    assert await callback(_FakeTool("tool-block"), {}, ctx) == {"error": _RULE_BLOCKED_ERROR}
    assert requested == []

    # 未命中规则的工具维持现状：信任照常放行
    ctx, requested = _fake_tool_context(None, state={SESSION_TRUST_KEY: "all"})
    assert await callback(_FakeTool("tool-req-none"), {}, ctx) is None
    assert requested == []


def test_escalation_permission_guard() -> None:
    """escalate 单据仅 admin 可批；非 escalate 与拒绝不受限。"""

    escalated = json.dumps({"escalated": True})
    with pytest.raises(PermissionError):
        approval_service.require_escalation_permission(escalated, is_admin=False)
    approval_service.require_escalation_permission(escalated, is_admin=True)
    approval_service.require_escalation_permission(json.dumps({}), is_admin=False)
    approval_service.require_escalation_permission(None, is_admin=False)


@pytest.mark.asyncio
async def test_create_pending_approval_marks_escalate_and_sla(tmp_path) -> None:
    """escalate 规则命中的单据打升级标记；临期（>80% 超时）出 sla_warning。"""

    engine, sessionmaker = _new_sessionmaker(tmp_path / "rules.db")
    try:
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        async with sessionmaker() as db:
            db.add(AppSetting(key="knowledge.approval_timeout_hours", value="10"))
            db.add(
                ChatSession(
                    id="s-esc",
                    title="t",
                    provider="p",
                    model="m",
                    agent_id="ops",
                    adk_user_id="u",
                )
            )
            await db.commit()
            await approval_rule_service.create_rule(
                db,
                name="高危工具升级",
                tool_pattern="mcp__obs-tools__acknowledge_alert",
                action="escalate",
            )
            row = await approval_service.create_pending_approval(
                db,
                session_id="s-esc",
                confirmation_fc_id="fc-1",
                tool_call_id="tc-1",
                tool_name="mcp__obs-tools__acknowledge_alert",
                hint="do it",
                arguments={"alert_id": "a-1"},
                trace_id="trace-1",
                turn_context={"provider": "p", "model": "m"},
            )
            payload = json.loads(row.payload)
            assert payload["escalated"] is True

            wire = approval_service.approval_to_wire(row, timeout_hours=10)
            assert wire["escalated"] is True
            assert wire["sla_warning"] is False

            # 未命中 escalate 规则的工具不打标记
            plain = await approval_service.create_pending_approval(
                db,
                session_id="s-esc",
                confirmation_fc_id="fc-2",
                tool_call_id="tc-2",
                tool_name="mcp__obs-tools__query_metric",
                hint="q",
                arguments={},
                trace_id="trace-1",
                turn_context={"provider": "p", "model": "m"},
            )
            assert "escalated" not in json.loads(plain.payload)

            # 回拨 created_at 到超时 80% 之外 → 临期提醒
            row.created_at = datetime.now(timezone.utc) - timedelta(hours=9)
            await db.commit()
            wire = approval_service.approval_to_wire(row, timeout_hours=10)
            assert wire["sla_warning"] is True

            await db.delete(row)
            await db.delete(plain)
            await db.delete(await db.get(ChatSession, "s-esc"))
            await db.commit()
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_rule_crud_validation(tmp_path) -> None:
    """非法取值显式失败；创建/更新/删除可用。"""

    engine, sessionmaker = _new_sessionmaker(tmp_path / "rules.db")
    try:
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        async with sessionmaker() as db:
            with pytest.raises(ValueError):
                await approval_rule_service.create_rule(
                    db, name="x", tool_pattern="t", action="skip"
                )
            row = await approval_rule_service.create_rule(
                db, name="免审", tool_pattern="run_python", action="auto_approve"
            )
            updated = await approval_rule_service.update_rule(
                db, row.id, action="block", priority=5
            )
            assert updated.action == "block" and updated.priority == 5
            with pytest.raises(LookupError):
                await approval_rule_service.update_rule(db, "nope", action="block")
            await approval_rule_service.delete_rule(db, row.id)
            with pytest.raises(LookupError):
                await approval_rule_service.delete_rule(db, row.id)
    finally:
        await engine.dispose()


class _Stop(Exception):
    """哨兵：fake build_chat_agent 记录入参后立刻中断生成器。"""


@pytest.mark.asyncio
async def test_stream_entries_forward_rule_actions(monkeypatch) -> None:
    """对话与审批续跑两个入口都把命中规则动作转发给闸门装配（否则一跑就 TypeError）。"""

    captured: list[dict] = []

    def fake_build_chat_agent(**kwargs):
        captured.append(kwargs)
        raise _Stop

    monkeypatch.setattr(
        "app.adk_runtime.stream_bridge.build_chat_agent", fake_build_chat_agent
    )
    rule_actions = {"mcp__obs-tools__query_metric": "require_approval"}
    common = dict(
        session_id="s1",
        user_id="u1",
        provider_id="deepseek",
        model="deepseek-flash",
        base_url="https://api.example.com/v1",
        api_key="test-key",
        session_service=SimpleNamespace(),
        agent_name="demo",
        agent_description="演示",
        persona_instruction="",
        thinking_enabled=False,
        rule_actions=rule_actions,
    )
    with pytest.raises(_Stop):
        await stream_adk_turn(user_text="hi", **common).__anext__()
    with pytest.raises(_Stop):
        await stream_adk_confirmation(
            confirmation_fc_id="fc-1", tool_call_id="c1", confirmed=True, **common
        ).__anext__()
    assert [c["rule_actions"] for c in captured] == [rule_actions, rule_actions]
