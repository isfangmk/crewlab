"""把 ADK Runner 事件桥接为前端 SSE 帧。

按模型事件时间线原样推送：先到的文本/工具先出，不做展示重排。
"""

from __future__ import annotations

import json
import logging
import time
from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from typing import Any  # noqa: I001 — used by extra_tools
from uuid import uuid4

from google.adk.agents._streaming_mode import StreamingMode
from google.adk.agents.run_config import RunConfig
from google.adk.runners import Runner
from google.adk.sessions import BaseSessionService
from google.adk.utils._function_call_names import REQUEST_CONFIRMATION_FUNCTION_CALL_NAME
from google.genai import types

from app.adk_runtime.agent_factory import build_chat_agent

logger = logging.getLogger(__name__)

APP_NAME = "ai_workbench"


@dataclass(frozen=True)
class SseFrame:
    """一条 SSE 事件。"""

    event: str
    data: dict[str, Any]


@dataclass
class _TurnBuffer:
    """单回合去重与气泡切分状态（不重排事件）。"""

    seen_tool_call_ids: set[str] = field(default_factory=set)
    seen_tool_response_ids: set[str] = field(default_factory=set)
    seen_confirmation_fc_ids: set[str] = field(default_factory=set)
    assistant_message_id: str = field(default_factory=lambda: str(uuid4()))
    thinking_message_id: str = field(default_factory=lambda: str(uuid4()))
    # 本段助理气泡是否已流出过正文；随后出现工具时换新气泡，避免后到的字写回工具上方
    answer_streamed_in_bubble: bool = False
    had_answer_text: bool = False
    final_partial_seen: bool = False
    # 与正文同理：跳过 ADK 终包对已推 partial 思考的整段回放
    final_thought_partial_seen: bool = False
    pending_tool_names: dict[str, str] = field(default_factory=dict)
    pending_tool_args: dict[str, dict[str, Any]] = field(default_factory=dict)
    # tool_call_id → monotonic 起点，用于统计耗时
    pending_tool_started_at: dict[str, float] = field(default_factory=dict)


def _answer_text_from_content(content: types.Content | None) -> str:
    """正文：排除 thought=True（reasoning 不进 assistant_delta）。"""

    if content is None or content.parts is None:
        return ""
    chunks: list[str] = []
    for part in content.parts:
        if getattr(part, "thought", False) is True:
            continue
        text = getattr(part, "text", None)
        if isinstance(text, str) and text != "":
            chunks.append(text)
    return "".join(chunks)


def _thought_text_from_content(content: types.Content | None) -> str:
    """思考链：仅 thought=True（DeepSeek reasoning_content）。"""

    if content is None or content.parts is None:
        return ""
    chunks: list[str] = []
    for part in content.parts:
        if getattr(part, "thought", False) is not True:
            continue
        text = getattr(part, "text", None)
        if isinstance(text, str) and text != "":
            chunks.append(text)
    return "".join(chunks)


def _function_calls(content: types.Content | None) -> list[Any]:
    if content is None or content.parts is None:
        return []
    out: list[Any] = []
    for part in content.parts:
        fc = getattr(part, "function_call", None)
        if fc is not None:
            out.append(fc)
    return out


def _function_responses(content: types.Content | None) -> list[Any]:
    if content is None or content.parts is None:
        return []
    out: list[Any] = []
    for part in content.parts:
        fr = getattr(part, "function_response", None)
        if fr is not None:
            out.append(fr)
    return out


def _answer_delta_frame(buf: _TurnBuffer, text: str, *, is_partial: bool) -> SseFrame | None:
    """按到达顺序推正文；跳过 ADK 终包对已推 partial 的整段回放。"""

    if text == "":
        return None
    if not is_partial and buf.final_partial_seen:
        return None
    if is_partial:
        buf.final_partial_seen = True
    buf.answer_streamed_in_bubble = True
    buf.had_answer_text = True
    return SseFrame(
        event="assistant_delta",
        data={
            "message_id": buf.assistant_message_id,
            "delta": text,
            "partial": is_partial,
        },
    )


def _thought_delta_frame(
    buf: _TurnBuffer,
    text: str,
    *,
    is_partial: bool,
    thinking_enabled: bool,
) -> SseFrame | None:
    """按到达顺序推思考；未开启或终包回放则不出帧。"""

    if text == "" or not thinking_enabled:
        return None
    if not is_partial and buf.final_thought_partial_seen:
        return None
    if is_partial:
        buf.final_thought_partial_seen = True
    return SseFrame(
        event="thinking_delta",
        data={
            "message_id": buf.thinking_message_id,
            "delta": text,
            "partial": is_partial,
        },
    )


def _begin_tool_cards(buf: _TurnBuffer) -> None:
    """工具卡出现前若本气泡已有正文，换新气泡承接后续文本。"""

    if not buf.answer_streamed_in_bubble:
        return
    buf.assistant_message_id = str(uuid4())
    buf.answer_streamed_in_bubble = False
    buf.final_partial_seen = False
    # 新气泡后思考另起一段，避免工具后的推理叠进工具前的思考卡
    buf.thinking_message_id = str(uuid4())
    buf.final_thought_partial_seen = False


def _tool_start_frame(
    buf: _TurnBuffer, call_id: str, name: str, args: dict[str, Any]
) -> SseFrame:
    buf.pending_tool_started_at[call_id] = time.monotonic()
    return SseFrame(
        event="tool_start",
        data={
            "tool_call_id": call_id,
            "name": name,
            "status": "pending",
            "arguments": args,
        },
    )


def _tool_end_frame(
    buf: _TurnBuffer,
    *,
    call_id: str,
    name: str,
    status: str,
    response: Any,
) -> SseFrame:
    started = buf.pending_tool_started_at.pop(call_id, None)
    if started is None:
        raise ValueError(f"tool_end 缺少开始时间: {call_id}")
    duration_ms = int((time.monotonic() - started) * 1000)
    error = response.get("error") if isinstance(response, dict) else None
    return SseFrame(
        event="tool_end",
        data={
            "tool_call_id": call_id,
            "name": name,
            "status": status,
            "duration_ms": duration_ms,
            "result": response,
            "error": error,
        },
    )


def _preview_text(text: str, limit: int = 80) -> str:
    if len(text) <= limit:
        return text
    return text[:limit] + "…"


def _usage_from_event(event: Any) -> tuple[int | None, int | None, int | None] | None:
    """读本事件 usage；无有效 token 字段则返回 None（禁止假 0）。"""

    meta = getattr(event, "usage_metadata", None)
    if meta is None:
        return None
    prompt = getattr(meta, "prompt_token_count", None)
    completion = getattr(meta, "candidates_token_count", None)
    total = getattr(meta, "total_token_count", None)
    if not isinstance(prompt, int) and not isinstance(completion, int) and not isinstance(total, int):
        return None
    return (
        prompt if isinstance(prompt, int) else None,
        completion if isinstance(completion, int) else None,
        total if isinstance(total, int) else None,
    )


async def _iter_runner_events(
    *,
    runner: Runner,
    user_id: str,
    session_id: str,
    new_message: types.Content,
    model: str,
    thinking_enabled: bool,
    seed_tool_started_at: dict[str, float] | None = None,
    max_llm_calls: int = 0,
) -> AsyncIterator[SseFrame]:
    """把一次 Runner.run_async 转成有序 SSE 帧。"""

    if model == "":
        raise ValueError("model 不能为空")
    run_config = RunConfig(streaming_mode=StreamingMode.SSE)
    # 助理级最大轮数：0 表示不限制；>0 时限制本回合的 LLM 调用次数
    if max_llm_calls > 0:
        run_config.max_llm_calls = max_llm_calls
    buf = _TurnBuffer()
    # 审批恢复：新缓冲没有 tool_start，用决策时刻作为耗时起点
    if seed_tool_started_at is not None:
        buf.pending_tool_started_at.update(seed_tool_started_at)
    adk_seq = 0
    sse_seq = 0
    # 累计 ADK usage；拿不到则 done 里显式 null，禁止假 0
    usage_prompt: int | None = None
    usage_completion: int | None = None
    usage_total: int | None = None

    def _absorb_usage(event: Any) -> None:
        nonlocal usage_prompt, usage_completion, usage_total
        piece = _usage_from_event(event)
        if piece is None:
            return
        prompt, completion, total = piece
        if prompt is not None:
            usage_prompt = (usage_prompt or 0) + prompt
        if completion is not None:
            usage_completion = (usage_completion or 0) + completion
        if total is not None:
            usage_total = (usage_total or 0) + total

    def _log_sse(frame: SseFrame) -> None:
        nonlocal sse_seq
        sse_seq += 1
        if frame.event == "assistant_delta":
            delta = frame.data["delta"]
            if not isinstance(delta, str):
                raise TypeError("assistant_delta.delta 必须是 str")
            logger.debug(
                "SSE出站 seq=%s session_id=%s event=%s message_id=%s partial=%s delta_len=%s preview=%s",
                sse_seq,
                session_id,
                frame.event,
                frame.data["message_id"],
                frame.data["partial"],
                len(delta),
                _preview_text(delta),
            )
            return
        if frame.event == "thinking_delta":
            delta = frame.data["delta"]
            if not isinstance(delta, str):
                raise TypeError("thinking_delta.delta 必须是 str")
            logger.debug(
                "SSE出站 seq=%s session_id=%s event=%s message_id=%s partial=%s delta_len=%s",
                sse_seq,
                session_id,
                frame.event,
                frame.data["message_id"],
                frame.data["partial"],
                len(delta),
            )
            return
        if frame.event in {"tool_start", "tool_end", "approval_pending"}:
            logger.debug(
                "SSE出站 seq=%s session_id=%s event=%s name=%s tool_call_id=%s",
                sse_seq,
                session_id,
                frame.event,
                frame.data.get("name") or frame.data.get("tool_name"),
                frame.data.get("tool_call_id"),
            )
            return
        logger.debug(
            "SSE出站 seq=%s session_id=%s event=%s",
            sse_seq,
            session_id,
            frame.event,
        )

    async for event in runner.run_async(
        user_id=user_id,
        session_id=session_id,
        new_message=new_message,
        run_config=run_config,
    ):
        adk_seq += 1
        _absorb_usage(event)
        calls = _function_calls(event.content)
        responses = _function_responses(event.content)
        inbound_text = _answer_text_from_content(event.content)
        inbound_thought = _thought_text_from_content(event.content)
        call_names = []
        for fc in calls:
            name = getattr(fc, "name", None)
            call_names.append(name if isinstance(name, str) else "?")
        if inbound_thought != "":
            logger.info(
                "ADK入站思考 seq=%s session_id=%s partial=%s calls=%s "
                "call_names=%s thought_len=%s",
                adk_seq,
                session_id,
                bool(event.partial),
                len(calls),
                call_names,
                len(inbound_thought),
            )
        else:
            logger.debug(
                "ADK入站 seq=%s session_id=%s partial=%s calls=%s call_names=%s "
                "responses=%s text_len=%s thought_len=%s text_preview=%s",
                adk_seq,
                session_id,
                bool(event.partial),
                len(calls),
                call_names,
                len(responses),
                len(inbound_text),
                len(inbound_thought),
                _preview_text(inbound_text) if inbound_text != "" else "",
            )
        if calls:
            is_partial = bool(event.partial)
            # 同帧可能同时带 thought + function_call；先出思考，不能被下面的 continue 丢掉
            thinking_frame = _thought_delta_frame(
                buf,
                inbound_thought,
                is_partial=is_partial,
                thinking_enabled=thinking_enabled,
            )
            if thinking_frame is not None:
                logger.info(
                    "工具同帧思考出站 session_id=%s thought_len=%s call_names=%s partial=%s",
                    session_id,
                    len(inbound_thought),
                    call_names,
                    is_partial,
                )
                _log_sse(thinking_frame)
                yield thinking_frame
            elif inbound_thought != "" and not thinking_enabled:
                logger.debug(
                    "已丢弃未开启思考的同帧 thought session_id=%s len=%s",
                    session_id,
                    len(inbound_thought),
                )
            # 同事件里若已有正文，先按时间线推出，再出工具卡
            inline = inbound_text
            answer_frame = _answer_delta_frame(buf, inline, is_partial=is_partial)
            if answer_frame is not None:
                _log_sse(answer_frame)
                yield answer_frame
            if is_partial:
                continue

            _begin_tool_cards(buf)

            for fc in calls:
                call_id = fc.id
                if not isinstance(call_id, str) or call_id == "":
                    raise ValueError("ADK function_call 缺少 id")
                name = fc.name
                if not isinstance(name, str) or name == "":
                    raise ValueError("ADK function_call 缺少 name")

                # ADK 人工确认请求：记录 confirmation_fc_id，发出审批卡
                if name == REQUEST_CONFIRMATION_FUNCTION_CALL_NAME:
                    if call_id in buf.seen_confirmation_fc_ids:
                        continue
                    args = fc.args
                    if not isinstance(args, dict):
                        raise TypeError("adk_request_confirmation.args 必须是 dict")
                    original = args.get("originalFunctionCall")
                    if not isinstance(original, dict):
                        raise ValueError("adk_request_confirmation 缺少 originalFunctionCall")
                    tool_call_id = original.get("id")
                    tool_name = original.get("name")
                    tool_args = original.get("args")
                    if not isinstance(tool_call_id, str) or tool_call_id == "":
                        raise ValueError("originalFunctionCall.id 无效")
                    if not isinstance(tool_name, str) or tool_name == "":
                        raise ValueError("originalFunctionCall.name 无效")
                    if tool_args is None:
                        tool_args = {}
                    if not isinstance(tool_args, dict):
                        raise TypeError("originalFunctionCall.args 必须是 dict")
                    # 卡片正文用工具实参；ADK toolConfirmation.hint 是内部英文提示，不给用户看
                    command_text = json.dumps(
                        {"tool": tool_name, "arguments": tool_args},
                        ensure_ascii=False,
                    )
                    buf.seen_confirmation_fc_ids.add(call_id)
                    buf.pending_tool_names[tool_call_id] = tool_name
                    buf.pending_tool_args[tool_call_id] = tool_args
                    if tool_call_id not in buf.seen_tool_call_ids:
                        buf.seen_tool_call_ids.add(tool_call_id)
                        start_frame = _tool_start_frame(buf, tool_call_id, tool_name, tool_args)
                        _log_sse(start_frame)
                        yield start_frame
                    approval_frame = SseFrame(
                        event="approval_pending",
                        data={
                            "approval_id": call_id,
                            "confirmation_fc_id": call_id,
                            "tool_call_id": tool_call_id,
                            "tool_name": tool_name,
                            "kind": "tool_confirmation",
                            "command": command_text,
                            "arguments": tool_args,
                        },
                    )
                    _log_sse(approval_frame)
                    yield approval_frame
                    continue

                if call_id in buf.seen_tool_call_ids:
                    continue
                args = fc.args
                if args is None:
                    args = {}
                if not isinstance(args, dict):
                    raise TypeError(f"function_call.args 必须是 dict: {name}")
                buf.seen_tool_call_ids.add(call_id)
                buf.pending_tool_names[call_id] = name
                buf.pending_tool_args[call_id] = args
                start_frame = _tool_start_frame(buf, call_id, name, args)
                _log_sse(start_frame)
                yield start_frame
            continue

        if responses and event.partial:
            continue
        for fr in responses:
            call_id = fr.id
            if not isinstance(call_id, str) or call_id == "":
                raise ValueError("ADK function_response 缺少 id")
            if call_id in buf.seen_tool_response_ids:
                continue
            name = fr.name
            if name == REQUEST_CONFIRMATION_FUNCTION_CALL_NAME:
                continue
            # 等待审批时 ADK 会先回 error 响应；真正执行后再回结果。同 id 允许第二次。
            response = fr.response
            if (
                isinstance(response, dict)
                and response.get("error")
                == "This tool call requires confirmation, please approve or reject."
            ):
                continue
            buf.seen_tool_response_ids.add(call_id)
            if not isinstance(name, str) or name == "":
                pending_name = buf.pending_tool_names.get(call_id)
                if not isinstance(pending_name, str) or pending_name == "":
                    raise ValueError(f"function_response 缺少 name: {call_id}")
                name = pending_name
            status = "error" if isinstance(response, dict) and "error" in response else "success"
            end_frame = _tool_end_frame(
                buf,
                call_id=call_id,
                name=name,
                status=status,
                response=response,
            )
            _log_sse(end_frame)
            yield end_frame

        is_partial = bool(event.partial)
        # 仅用户开启思考时推 thinking_delta；未开启则丢弃 thought，避免前端出现思考气泡
        thinking_frame = _thought_delta_frame(
            buf,
            inbound_thought,
            is_partial=is_partial,
            thinking_enabled=thinking_enabled,
        )
        if thinking_frame is not None:
            _log_sse(thinking_frame)
            yield thinking_frame
        elif inbound_thought != "" and not thinking_enabled:
            logger.debug(
                "已丢弃未开启思考的 thought 片段 session_id=%s len=%s",
                session_id,
                len(inbound_thought),
            )

        text = inbound_text
        answer_frame = _answer_delta_frame(buf, text, is_partial=is_partial)
        if answer_frame is not None:
            _log_sse(answer_frame)
            yield answer_frame

    done_frame = SseFrame(
        event="done",
        data={
            "session_id": session_id,
            "assistant_message": {
                "id": buf.assistant_message_id,
                "content": "",
                "created_at": "",
            },
            "tool_calls": [],
            "usage": {
                "prompt_tokens": usage_prompt,
                "completion_tokens": usage_completion,
                "total_tokens": usage_total,
            },
            "iterations": 1,
            "had_final_text": buf.had_answer_text,
        },
    )
    _log_sse(done_frame)
    yield done_frame


def _user_content_with_images(
    user_text: str, image_parts: list[tuple[bytes, str]] | None
) -> types.Content:
    """组装用户多模态 Content：正文 + 图片 bytes。"""

    parts: list[types.Part] = []
    if user_text != "":
        parts.append(types.Part.from_text(text=user_text))
    if image_parts is not None:
        for raw, mime_type in image_parts:
            if not isinstance(raw, (bytes, bytearray)) or len(raw) == 0:
                raise ValueError("图片附件内容为空")
            if not isinstance(mime_type, str) or mime_type == "":
                raise ValueError("图片附件缺少 mime_type")
            parts.append(types.Part.from_bytes(data=bytes(raw), mime_type=mime_type))
    if len(parts) == 0:
        raise ValueError("用户消息不能既无正文又无附件")
    return types.Content(role="user", parts=parts)


async def stream_adk_turn(
    *,
    session_id: str,
    user_id: str,
    user_text: str,
    provider_id: str,
    model: str,
    base_url: str,
    api_key: str,
    session_service: BaseSessionService,
    agent_name: str,
    agent_description: str,
    persona_instruction: str,
    thinking_enabled: bool,
    extra_tools: list[Any] | None = None,
    llm_timeout_seconds: int = 120,
    confirm_tool_names: set[str] | None = None,
    model_prefix: str = "",
    provider_extra_body: dict[str, Any] | None = None,
    disallowed_tool_names: set[str] | None = None,
    max_turns: int = 0,
    image_parts: list[tuple[bytes, str]] | None = None,
) -> AsyncIterator[SseFrame]:
    """跑一轮用户对话；可附带图片多模态 parts。"""

    agent = build_chat_agent(
        provider_id=provider_id,
        model=model,
        base_url=base_url,
        api_key=api_key,
        agent_name=agent_name,
        agent_description=agent_description,
        persona_instruction=persona_instruction,
        thinking_enabled=thinking_enabled,
        extra_tools=extra_tools,
        llm_timeout_seconds=llm_timeout_seconds,
        confirm_tool_names=confirm_tool_names,
        model_prefix=model_prefix,
        provider_extra_body=provider_extra_body,
        disallowed_tool_names=disallowed_tool_names,
        max_turns=max_turns,
    )
    runner = Runner(agent=agent, app_name=APP_NAME, session_service=session_service)
    existing = await session_service.get_session(
        app_name=APP_NAME, user_id=user_id, session_id=session_id
    )
    if existing is None:
        await session_service.create_session(
            app_name=APP_NAME, user_id=user_id, session_id=session_id
        )
        logger.info("创建 ADK session session_id=%s user_id=%s", session_id, user_id)

    message = _user_content_with_images(user_text, image_parts)
    logger.info(
        "ADK 用户消息 parts session_id=%s text_len=%s image_count=%s",
        session_id,
        len(user_text),
        0 if image_parts is None else len(image_parts),
    )
    try:
        async for frame in _iter_runner_events(
            runner=runner,
            user_id=user_id,
            session_id=session_id,
            new_message=message,
            model=model,
            thinking_enabled=thinking_enabled,
            max_llm_calls=max_turns,
        ):
            yield frame
    finally:
        # Runner.close() 是唯一会关闭 toolset（MCP session manager）的路径
        await runner.close()


async def stream_adk_confirmation(
    *,
    session_id: str,
    user_id: str,
    confirmation_fc_id: str,
    tool_call_id: str,
    confirmed: bool,
    provider_id: str,
    model: str,
    base_url: str,
    api_key: str,
    session_service: BaseSessionService,
    agent_name: str,
    agent_description: str,
    persona_instruction: str,
    thinking_enabled: bool,
    extra_tools: list[Any] | None = None,
    llm_timeout_seconds: int = 120,
    confirm_tool_names: set[str] | None = None,
    model_prefix: str = "",
    provider_extra_body: dict[str, Any] | None = None,
    disallowed_tool_names: set[str] | None = None,
    max_turns: int = 0,
) -> AsyncIterator[SseFrame]:
    """用 ADK FunctionResponse 恢复被确认闸门拦住的回合。"""

    if tool_call_id == "":
        raise ValueError("tool_call_id 不能为空")
    agent = build_chat_agent(
        provider_id=provider_id,
        model=model,
        base_url=base_url,
        api_key=api_key,
        agent_name=agent_name,
        agent_description=agent_description,
        persona_instruction=persona_instruction,
        thinking_enabled=thinking_enabled,
        extra_tools=extra_tools,
        llm_timeout_seconds=llm_timeout_seconds,
        confirm_tool_names=confirm_tool_names,
        model_prefix=model_prefix,
        provider_extra_body=provider_extra_body,
        disallowed_tool_names=disallowed_tool_names,
        max_turns=max_turns,
    )
    runner = Runner(agent=agent, app_name=APP_NAME, session_service=session_service)
    existing = await session_service.get_session(
        app_name=APP_NAME, user_id=user_id, session_id=session_id
    )
    if existing is None:
        raise LookupError(f"ADK session 不存在: {session_id}")

    message = types.Content(
        role="user",
        parts=[
            types.Part(
                function_response=types.FunctionResponse(
                    id=confirmation_fc_id,
                    name=REQUEST_CONFIRMATION_FUNCTION_CALL_NAME,
                    response={"confirmed": confirmed},
                )
            )
        ],
    )
    logger.info(
        "恢复审批 session_id=%s confirmation_fc_id=%s tool_call_id=%s confirmed=%s",
        session_id,
        confirmation_fc_id,
        tool_call_id,
        confirmed,
    )
    try:
        async for frame in _iter_runner_events(
            runner=runner,
            user_id=user_id,
            session_id=session_id,
            new_message=message,
            model=model,
            thinking_enabled=thinking_enabled,
            seed_tool_started_at={tool_call_id: time.monotonic()},
            max_llm_calls=max_turns,
        ):
            yield frame
    finally:
        # Runner.close() 是唯一会关闭 toolset（MCP session manager）的路径
        await runner.close()


def frame_to_sse(frame: SseFrame) -> str:
    """编码为 SSE 文本。"""

    return f"event: {frame.event}\ndata: {json.dumps(frame.data, ensure_ascii=False)}\n\n"
