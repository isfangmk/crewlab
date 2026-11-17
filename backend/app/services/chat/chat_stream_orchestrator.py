"""对话回合后台生产编排：把 stream_session_message 的帧推到 Redis 帧总线。

浏览器聊天路径（POST /messages/stream）走这里：请求只负责建回合、起后台任务、
立即返回 turn_id；真正的 LLM 回合在后台跑，逐帧 publish 到 chat_stream_bus。
所有订阅者（含发起标签页）统一从 GET /events 读流，实现多标签页补发+续推。

为什么另起编排而不改 stream_session_message：后者有 5 个调用方（publish/eval/
trigger/edit-resend），都依赖它的"请求作用域内产出完整流"语义；保持它纯生成器
不变，只在浏览器路径加一层，兼容所有既有调用方。
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from app.db import SessionLocal
from app.request_context import set_session_id
from app.services.chat import chat_service, chat_stream_bus
from app.services.platform import notification_bus

logger = logging.getLogger(__name__)

# 后台任务强引用，防事件循环 GC 回收；done 回调里移除
_tasks: set[asyncio.Task[None]] = set()


def _spawn(coro: Any) -> None:
    task = asyncio.create_task(coro)
    _tasks.add(task)
    task.add_done_callback(_tasks.discard)


async def begin_streamed_turn(
    *,
    session_id: str,
    content: str,
    provider: str | None,
    model: str | None,
    client_message_id: str | None,
    thinking_enabled: bool,
    web_search_enabled: bool,
    attachment_ids: list[str],
    locale: str | None,
    mentions: list[dict[str, Any]] | None,
    owner_uid: int | None = None,
    before_produce: Callable[[AsyncSession], Awaitable[None]] | None = None,
) -> str:
    """登记回合 + 启动后台生产，返回 turn_id（HTTP 立即响应）。

    before_produce 在后台独立 session 内、生产开始前执行（如编辑重发的截断+审计），
    让这类前置操作也脱离请求作用域；失败会作为 error 帧发布。
    """

    turn_id = await chat_stream_bus.start_turn(session_id)
    _spawn(
        _produce(
            session_id=session_id,
            turn_id=turn_id,
            content=content,
            provider=provider,
            model=model,
            client_message_id=client_message_id,
            thinking_enabled=thinking_enabled,
            web_search_enabled=web_search_enabled,
            attachment_ids=attachment_ids,
            locale=locale,
            mentions=mentions,
            owner_uid=owner_uid,
            before_produce=before_produce,
        )
    )
    return turn_id


async def _produce(
    *,
    session_id: str,
    turn_id: str,
    content: str,
    provider: str | None,
    model: str | None,
    client_message_id: str | None,
    thinking_enabled: bool,
    web_search_enabled: bool,
    attachment_ids: list[str],
    locale: str | None,
    mentions: list[dict[str, Any]] | None,
    owner_uid: int | None = None,
    before_produce: Callable[[AsyncSession], Awaitable[None]] | None = None,
) -> None:
    """后台消费 stream_session_message，逐帧 publish；异常也发 error 终态帧。"""

    # 后台任务不继承请求上下文：显式设置 sid 让日志可追溯
    set_session_id(session_id)
    terminal = "finished"
    try:
        # 独立 session：请求作用域的 db 会随 HTTP 响应结束而关闭，后台必须自建。
        async with SessionLocal() as db:
            if before_produce is not None:
                await before_produce(db)
            async for frame in chat_service.stream_session_message(
                db,
                session_id=session_id,
                content=content,
                provider=provider,
                model=model,
                client_message_id=client_message_id,
                thinking_enabled=thinking_enabled,
                web_search_enabled=web_search_enabled,
                attachment_ids=attachment_ids,
                locale=locale,
                mentions=mentions,
            ):
                await chat_stream_bus.publish(
                    turn_id,
                    event=frame.event,
                    data=frame.data,
                )
                if frame.event in ("done", "error"):
                    terminal = frame.event
    except Exception as exc:  # noqa: BLE001 - 生产异常必须发 error 终态，否则订阅者永久挂起
        logger.exception("对话回合后台生产失败 session_id=%s turn_id=%s", session_id, turn_id)
        terminal = "error"
        try:
            await chat_stream_bus.publish(
                turn_id,
                event="error",
                data={"error": str(exc)},
            )
        except Exception:  # noqa: BLE001 - 连 Redis 都挂了就只能记日志
            logger.exception("发布错误终态帧失败 session_id=%s", session_id)
    finally:
        await chat_stream_bus.finish_turn(session_id, status=terminal)
        # 回合落库完成：通知该用户所有标签页重拉消息列表（取代 5s 轮询）。
        # 发起标签页自己也在读本回合 SSE，但通知让它及其他标签页都能刷新侧栏/统计。
        await notification_bus.publish(
            owner_uid,
            kind="chat_refetch",
            payload={"reason": "turn_finished", "session_id": session_id, "terminal": terminal},
        )
        logger.info(
            "对话回合后台生产结束 session_id=%s turn_id=%s terminal=%s",
            session_id,
            turn_id,
            terminal,
        )
