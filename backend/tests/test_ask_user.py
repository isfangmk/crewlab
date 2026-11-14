"""ask_user 澄清交互：参数校验、暂停/恢复骨架与真实会话往返。"""

from __future__ import annotations

import json
import uuid
from types import SimpleNamespace

import httpx
import pytest
from google.adk.tools.tool_confirmation import ToolConfirmation
from sqlalchemy import select

from app.adk_runtime.tool_approval import (
    _PENDING_CONFIRMATION_ERROR,
    _REJECTED_ERROR,
    build_before_tool_callback,
)
from app.db import create_db_engine
from app.models_orm.approval import Approval
from app.services import approval_service
from app.tools.user_input import (
    ask_user,
    coerce_ask_display,
    normalize_ask_spec,
    normalize_user_answer,
    validate_answer,
)

OPTIONS = ["周一", "周三", "周五"]


def _fake_tool_context(confirmation: ToolConfirmation | None):
    """手写 tool_context 双替身（同 test_adk_replay 的 SimpleNamespace 风格）。"""

    requested: list[str] = []

    def request_confirmation(hint: str = "") -> None:
        requested.append(hint)

    ctx = SimpleNamespace(
        tool_confirmation=confirmation,
        actions=SimpleNamespace(skip_summarization=False),
        function_call_id=f"call_{uuid.uuid4().hex[:8]}",
        request_confirmation=request_confirmation,
    )
    return ctx, requested


# --- 工具本体：答案经 ToolConfirmation.payload 回流 ---


@pytest.mark.asyncio
async def test_ask_user_returns_confirmed_payload() -> None:
    confirmation = ToolConfirmation(
        confirmed=True, payload={"selected": "周三", "values": {}, "text": ""}
    )
    ctx, _ = _fake_tool_context(confirmation)
    result = await ask_user(
        question="想预订周几？", options=OPTIONS, tool_context=ctx
    )
    assert result == {"selected": "周三", "values": {}, "text": ""}


@pytest.mark.asyncio
async def test_ask_user_returns_field_values() -> None:
    fields = [
        {"key": "title", "label": "会议主题", "required": True},
        {"key": "note", "label": "备注"},
    ]
    confirmation = ToolConfirmation(
        confirmed=True,
        payload={"selected": None, "values": {"title": "周会", "note": ""}, "text": ""},
    )
    ctx, _ = _fake_tool_context(confirmation)
    result = await ask_user(question="填写会议信息", fields=fields, tool_context=ctx)
    assert result == {"selected": None, "values": {"title": "周会", "note": ""}, "text": ""}


@pytest.mark.asyncio
async def test_ask_user_rejects_bad_answer() -> None:
    ctx, _ = _fake_tool_context(
        ToolConfirmation(confirmed=True, payload={"selected": "周日", "values": {}, "text": ""})
    )
    with pytest.raises(ValueError):
        await ask_user(question="想预订周几？", options=OPTIONS, tool_context=ctx)

    ctx, _ = _fake_tool_context(
        ToolConfirmation(confirmed=True, payload={"selected": "周三", "extra": 1})
    )
    with pytest.raises(ValueError):
        await ask_user(question="想预订周几？", options=OPTIONS, tool_context=ctx)


@pytest.mark.asyncio
async def test_ask_user_manual_run_is_rejected() -> None:
    # 手动运行没有确认通道：不猜答案，显式报错
    result = await ask_user(question="想预订周几？", options=OPTIONS, tool_context=None)
    assert result == {"error": "ask_user 需要用户在对话卡片中作答，不支持直接运行"}


# --- 提问规格与回答规范化 ---


def test_normalize_ask_spec_rejects_empty_spec() -> None:
    with pytest.raises(ValueError):
        normalize_ask_spec("", ["a"], None)
    # 选项与表单都没有：应改用纯文字提问
    with pytest.raises(ValueError):
        normalize_ask_spec("问题", [], [])
    question, options, fields = normalize_ask_spec(
        " 问题 ", ["周一", "", "周三"], [{"key": "k", "label": ""}]
    )
    assert question == "问题"
    assert options == ["周一", "周三"]
    assert fields == [{"key": "k", "label": "k"}]


def test_validate_answer_contract() -> None:
    answer = normalize_user_answer({"selected": "周三", "values": {}, "text": ""})
    validate_answer(answer, OPTIONS, [])
    with pytest.raises(ValueError):
        validate_answer(
            normalize_user_answer({"selected": None, "values": {}, "text": ""}), OPTIONS, []
        )
    fields = [{"key": "title", "label": "主题", "required": True}]
    with pytest.raises(ValueError):
        validate_answer(
            normalize_user_answer({"values": {"title": ""}, "text": ""}), [], fields
        )
    # 无选项无表单：文本必填
    with pytest.raises(ValueError):
        validate_answer(normalize_user_answer({"text": " "}), [], [])
    with pytest.raises(TypeError):
        normalize_user_answer("周三")


def test_coerce_ask_display_never_raises() -> None:
    display = coerce_ask_display(
        {"question": 123, "options": "不是列表", "fields": [{"bad": True}, "x"]}
    )
    assert display == {"question": "123", "options": [], "fields": []}


# --- 暂停/恢复闸门 ---


@pytest.mark.asyncio
async def test_before_tool_pauses_ask_user_without_confirmation() -> None:
    callback = build_before_tool_callback(set(), set())
    tool = SimpleNamespace(name="ask_user")
    ctx, requested = _fake_tool_context(None)
    result = await callback(tool, {"question": "q", "options": ["a"]}, ctx)
    assert result == {"error": _PENDING_CONFIRMATION_ERROR}
    assert len(requested) == 1
    assert ctx.actions.skip_summarization is True


@pytest.mark.asyncio
async def test_before_tool_releases_ask_user_after_confirmation() -> None:
    callback = build_before_tool_callback(set(), set())
    tool = SimpleNamespace(name="ask_user")
    ok, _ = _fake_tool_context(ToolConfirmation(confirmed=True, payload={"text": "hi"}))
    assert await callback(tool, {"question": "q", "options": ["a"]}, ok) is None

    rejected, requested = _fake_tool_context(ToolConfirmation(confirmed=False))
    assert await callback(tool, {"question": "q", "options": ["a"]}, rejected) == {
        "error": _REJECTED_ERROR
    }
    assert requested == []


@pytest.mark.asyncio
async def test_before_tool_rejects_bad_ask_spec_without_pause() -> None:
    callback = build_before_tool_callback(set(), set())
    tool = SimpleNamespace(name="ask_user")
    ctx, requested = _fake_tool_context(None)
    result = await callback(tool, {"question": ""}, ctx)
    assert isinstance(result, dict)
    assert "参数非法" in result["error"]
    assert requested == []


def test_tool_confirmation_wire_accepts_payload() -> None:
    # ToolConfirmation 是 extra=forbid：回答必须走 payload 字段，
    # 改成别的键名（如 answer）会在恢复时被 pydantic 拒绝
    parsed = ToolConfirmation.from_response_dict(
        {"confirmed": True, "payload": {"selected": "周三"}}
    )
    assert parsed.confirmed is True
    assert parsed.payload == {"selected": "周三"}


# --- 决策端校验（真实 DB，仿 test_approval_claim 的自建自弃） ---


def _new_sessionmaker():
    from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

    from app.config import get_settings

    engine = create_db_engine(get_settings().database_url)
    return engine, async_sessionmaker(engine, expire_on_commit=False)


def _ask_row() -> Approval:
    return Approval(
        id=f"apr_{uuid.uuid4().hex[:12]}",
        session_id=f"s_{uuid.uuid4().hex[:8]}",
        confirmation_fc_id=f"fc_{uuid.uuid4().hex[:12]}",
        tool_call_id=f"call_{uuid.uuid4().hex[:8]}",
        tool_name="ask_user",
        kind="user_input",
        title="需要你的输入",
        summary="需要你的输入",
        payload=json.dumps(
            {"arguments": {"question": "想预订周几？", "options": OPTIONS}},
            ensure_ascii=False,
        ),
        hint="",
        status="pending",
    )


@pytest.mark.asyncio
async def test_stream_decide_validates_answer_before_claim() -> None:
    engine, sessionmaker = _new_sessionmaker()
    try:
        async with sessionmaker() as db:
            row = _ask_row()
            db.add(row)
            await db.commit()

            # 澄清提问审批必须携带 answer；缺 answer 不得认领
            with pytest.raises(ValueError):
                async for _frame in approval_service.stream_decide(
                    db, approval_id=row.id, confirmed=True, reason=""
                ):
                    pass
            # 回答不命中候选：同样不得认领
            with pytest.raises(ValueError):
                async for _frame in approval_service.stream_decide(
                    db,
                    approval_id=row.id,
                    confirmed=True,
                    reason="",
                    answer={"selected": "周日", "values": {}, "text": ""},
                ):
                    pass
            await db.refresh(row)
            assert row.status == "pending"

            # 普通审批不接受 answer
            row.kind = "tool_confirmation"
            await db.commit()
            with pytest.raises(ValueError):
                async for _frame in approval_service.stream_decide(
                    db,
                    approval_id=row.id,
                    confirmed=True,
                    reason="",
                    answer={"selected": "周三", "values": {}, "text": ""},
                ):
                    pass

            await db.delete(row)
            await db.commit()
    finally:
        await engine.dispose()


# --- 真实会话往返：提问 → 答案 → 助理续答（依赖运行中的后端与真实模型） ---


async def _collect_frames(
    client: httpx.AsyncClient, method: str, url: str, body: dict
) -> list[tuple[str, dict]]:
    frames: list[tuple[str, dict]] = []
    async with client.stream(method, url, json=body) as r:
        r.raise_for_status()
        event_name = ""
        async for line in r.aiter_lines():
            if line.startswith("event:"):
                event_name = line[6:].strip()
                continue
            if not line.startswith("data:"):
                continue
            raw = line[5:].strip()
            if raw == "":
                continue
            frames.append((event_name, json.loads(raw)))
    return frames


@pytest.mark.asyncio
async def test_ask_user_round_trip_in_live_chat() -> None:
    from tests.conftest import load_env, login_access_token

    env = load_env()
    host = env.get("HOST", "127.0.0.1")
    port = env.get("PORT", "18080")
    base = f"http://{host}:{port}"
    session_id = ""
    engine, sessionmaker = _new_sessionmaker()
    try:
        async with httpx.AsyncClient(base_url=base, timeout=180.0) as client:
            health = await client.get("/healthz")
            if health.status_code != 200:
                pytest.skip(f"后端未就绪 status={health.status_code}")
            token = await login_access_token(client, env)
            client.headers["Authorization"] = f"Bearer {token}"

            # 各环境启用的对话模型不同，按候选逐个试探第一个能建会话的组合
            session_id = ""
            last_error = ""
            for provider, model in (
                ("deepseek", "deepseek-flash"),
                ("custom", "workbuddy/glm-5.3-flash"),
            ):
                cr = await client.post(
                    "/api/v1/chat/sessions",
                    json={
                        "title": f"ask-user-test-{uuid.uuid4().hex[:6]}",
                        "provider": provider,
                        "model": model,
                        "agent_id": "default",
                    },
                )
                if cr.status_code == 200:
                    session_id = cr.json()["id"]
                    break
                last_error = cr.text
            assert session_id != "", f"没有可用的对话模型组合: {last_error}"

            prompt = (
                "这是一次工具调用测试：请立刻调用 ask_user 工具向我提问，"
                'question="你想预订周几？"，options=["周一", "周三", "周五"]。'
                "必须调用 ask_user 工具，禁止用文字直接提问，不要调用其他工具。"
            )
            # 模型偶发不听话：最多重试 3 轮直到出现澄清卡
            pending: dict | None = None
            for _ in range(3):
                frames = await _collect_frames(
                    client,
                    "POST",
                    f"/api/v1/chat/sessions/{session_id}/messages/stream",
                    {
                        "content": prompt,
                        "thinking_enabled": False,
                        "web_search_enabled": False,
                        "mentions": [],
                        "attachment_ids": [],
                    },
                )
                for event_name, data in frames:
                    if event_name == "approval_pending" and data.get("kind") == "user_input":
                        pending = data
                        break
                if pending is not None:
                    break
            assert pending is not None, "ask_user 澄清卡未出现"
            assert pending["tool_name"] == "ask_user"
            assert "question" in pending["arguments"]

            approval_id = pending["approval_id"]

            # 坏参防护：答案不命中候选 → 报错且审批单不得被认领
            bad = await _collect_frames(
                client,
                "POST",
                f"/api/v1/approvals/{approval_id}/approve/stream",
                {"reason": "", "answer": {"selected": "周日", "values": {}, "text": ""}},
            )
            assert any(event == "error" for event, _ in bad), bad
            row_resp = await client.get(f"/api/v1/approvals/{approval_id}")
            row_resp.raise_for_status()
            assert row_resp.json()["status"] == "pending"

            # 好答案：工具回传答案，助理续答
            frames = await _collect_frames(
                client,
                "POST",
                f"/api/v1/approvals/{approval_id}/approve/stream",
                {"reason": "", "answer": {"selected": "周三", "values": {}, "text": ""}},
            )
            tool_ends = [data for event, data in frames if event == "tool_end"]
            assert tool_ends, frames
            result_text = json.dumps(tool_ends[-1].get("result"), ensure_ascii=False)
            assert "周三" in result_text
            assert any(event == "assistant_delta" for event, _ in frames), frames
            done = [data for event, data in frames if event == "approval_done"]
            assert done and done[0]["status"] == "executed"

            # 用户回答落 payload，可事后追溯
            row_resp = await client.get(f"/api/v1/approvals/{approval_id}")
            row_resp.raise_for_status()
            payload = json.loads(row_resp.json()["payload"])
            assert payload["answer"]["selected"] == "周三"
    except httpx.ConnectError as exc:
        pytest.skip(f"无法连接后端 {base}: {exc}")
    finally:
        if session_id != "":
            try:
                async with httpx.AsyncClient(base_url=base, timeout=30.0) as client:
                    token = await login_access_token(client, env)
                    client.headers["Authorization"] = f"Bearer {token}"
                    await client.delete(f"/api/v1/chat/sessions/{session_id}")
            except httpx.HTTPError:
                pass
        # 审批行不随会话删除级联，直接清掉
        try:
            async with sessionmaker() as db:
                rows = (
                    await db.scalars(
                        select(Approval).where(Approval.session_id == session_id)
                    )
                ).all()
                for row in rows:
                    await db.delete(row)
                await db.commit()
        finally:
            await engine.dispose()
