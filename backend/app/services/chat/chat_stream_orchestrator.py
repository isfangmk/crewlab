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
from app.services.chat import chat_queue, chat_service, chat_stream_bus
from app.services.platform import notification_bus, redis_distributed

logger = logging.getLogger(__name__)

# 后台任务强引用，防事件循环 GC 回收；done 回调里移除
_tasks: set[asyncio.Task[None]] = set()


def _spawn(coro: Any) -> None:
    task = asyncio.create_task(coro)
    _tasks.add(task)
    task.add_done_callback(_tasks.discard)


# 插话真打断：会话 → 该会话当前所有活跃回合的 abort_event（正常同一时刻一个）。
# event 是进程内对象，跨 worker 的中断请求经 Redis flag:signal 广播到本进程 hook。
_abort_events: dict[str, set[asyncio.Event]] = {}


def _fire_local_interrupt(session_id: str) -> bool:
    """置位本进程该会话的所有活跃 abort_event；返回是否命中活跃回合。"""

    events = _abort_events.get(session_id)
    if not events:
        return False
    for event in list(events):
        event.set()
    return True


def _register_abort_event(session_id: str, event: asyncio.Event) -> None:
    _abort_events.setdefault(session_id, set()).add(event)


def _unregister_abort_event(session_id: str, event: asyncio.Event) -> None:
    events = _abort_events.get(session_id)
    if events is None:
        return
    events.discard(event)
    if not events:
        _abort_events.pop(session_id, None)


# 跨 worker 广播回调：其他 worker 发来的 chat-interrupt:{sid} 触发本进程置位。
redis_distributed.register_interrupt_hook(_fire_local_interrupt)


async def request_interrupt(session_id: str) -> bool:
    """请求打断某会话当前回合：本进程立即置位 + 广播给其他 worker。

    返回是否在本进程命中活跃回合（跨 worker 的命中无法同步得知，返回 False 不代表没打断）。
    """

    local_hit = _fire_local_interrupt(session_id)
    try:
        await redis_distributed.set_flag(f"chat-interrupt:{session_id}", True)
    except Exception:  # noqa: BLE001 - 广播失败不能影响本地中断
        logger.exception("插话中断广播失败 session_id=%s", session_id)
    logger.info("插话中断请求 session_id=%s local_hit=%s", session_id, local_hit)
    return local_hit


def _decorate_terminal_frame(
    event: str, data: dict[str, Any], *, interrupted: bool = False
) -> dict[str, Any]:
    """终态帧挂钩点：插话打断时在此注入 interrupted 字段。

    所有 done/error 帧统一经此，前端与订阅者按此字段判断是否为打断收尾；
    非终态帧原样返回。
    """

    if event not in ("done", "error"):
        return data
    decorated = dict(data)
    decorated.setdefault("interrupted", interrupted)
    return decorated


async def _drain_one_queued_turn(session_id: str) -> None:
    """回合结束后原子取下一条队列消息并起新回合；队列空则停。

    - 出队是原子的（Lua）：多 worker 不会重复消费，取不到直接返回；
    - 新回合用 _spawn 起后台任务，其 finally 会再次调用本函数，形成链式直到空；
      每次只起一个新回合，不会在同一回合内无限循环；
    - 空正文的脏项跳过继续取（出队已移除本条，循环次数受队列长度约束，不会死循环）。
    """

    while True:
        # 清除上一回合可能残留的插话广播标志，避免跨 worker 新回合被旧信号影响
        try:
            await redis_distributed.set_flag(f"chat-interrupt:{session_id}", False)
        except Exception:  # noqa: BLE001 - 清标志失败不阻断消费
            logger.exception("清除插话中断标志失败 session_id=%s", session_id)
        try:
            item = await chat_queue.dequeue(session_id)
        except Exception:  # noqa: BLE001 - 出队失败不能拖垮回合收尾
            logger.exception("队列出队失败 session_id=%s", session_id)
            return
        if item is None:
            return

        try:
            remaining = await chat_queue.total(session_id)
        except Exception:  # noqa: BLE001 - 统计失败不阻断消费，total 退化为 0
            logger.exception("队列长度统计失败 session_id=%s", session_id)
            remaining = 0
        await notification_bus.publish(
            item.owner_uid,
            kind="queue_changed",
            payload={"session_id": session_id, "action": "dequeued", "total": remaining},
        )

        if not item.content:
            logger.warning(
                "队列项正文为空，跳过起回合 session_id=%s item_id=%s",
                session_id,
                item.item_id,
            )
            continue

        logger.info(
            "队列出队起新回合 session_id=%s item_id=%s owner_uid=%s",
            session_id,
            item.item_id,
            item.owner_uid,
        )
        _spawn(
            begin_streamed_turn(
                session_id=session_id,
                content=item.content,
                provider=item.provider,
                model=item.model,
                client_message_id=None,
                thinking_enabled=item.thinking_enabled,
                web_search_enabled=item.web_search_enabled,
                attachment_ids=item.attachment_ids,
                locale=None,
                mentions=item.mentions or None,
                owner_uid=item.owner_uid,
            )
        )
        return


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
    # 插话真打断：本回合的 abort_event 注册到进程内表，供 request_interrupt / 跨 worker hook 置位
    abort_event = asyncio.Event()
    _register_abort_event(session_id, abort_event)
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
                abort_signal=abort_event,
            ):
                await chat_stream_bus.publish(
                    turn_id,
                    event=frame.event,
                    data=_decorate_terminal_frame(
                        frame.event, frame.data, interrupted=abort_event.is_set()
                    ),
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
                data=_decorate_terminal_frame(
                    "error", {"error": str(exc)}, interrupted=abort_event.is_set()
                ),
            )
        except Exception:  # noqa: BLE001 - 连 Redis 都挂了就只能记日志
            logger.exception("发布错误终态帧失败 session_id=%s", session_id)
    finally:
        _unregister_abort_event(session_id, abort_event)
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
        # 当前回合结束后，依次消费排队消息：链式起新回合，直到队列空。
        # 放在 chat_refetch 通知之后，保证前端先收到本回合落库信号再处理后续回合。
        await _drain_one_queued_turn(session_id)
