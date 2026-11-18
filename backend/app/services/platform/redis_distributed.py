"""跨 worker 协同原语：Redis 分布式锁 + 停止信号广播。

- RedisLock：SET NX PX + Lua 校验值释放，用于渠道回合串行化与常驻循环 leader 选举。
- 停止信号经 Redis PubSub 广播：request_stop 本地置位 + publish，各 worker 订阅后
  同步置本地集合，is_stopped 仍可同步高频调用（ADK stop_check 要求同步 callable）。
"""

from __future__ import annotations

import asyncio
import json
import logging
import uuid
from collections.abc import AsyncIterator, Awaitable
from contextlib import asynccontextmanager
from types import TracebackType
from typing import Any, cast

from app.redis import get_redis

logger = logging.getLogger(__name__)

_RELEASE_SCRIPT = """
if redis.call('get', KEYS[1]) == ARGV[1] then
    return redis.call('del', KEYS[1])
else
    return 0
end
"""


class RedisLock:
    """Redis 分布式锁（async with）；释放用 Lua 校验唯一值，绝不误删他人锁。"""

    def __init__(self, name: str, *, ttl_ms: int = 60_000) -> None:
        self._key = f"lock:{name}"
        self._ttl_ms = ttl_ms
        self._token = uuid.uuid4().hex

    async def acquire(self) -> bool:
        redis = get_redis()
        return bool(await redis.set(self._key, self._token, nx=True, px=self._ttl_ms))

    async def release(self) -> None:
        redis = get_redis()
        try:
            # redis-py 的 eval 类型桩在同步/异步重载间歧义，运行时是 async
            await cast(Awaitable[Any], redis.eval(_RELEASE_SCRIPT, 1, self._key, self._token))
        except Exception:  # noqa: BLE001 - 释放失败只记日志，不掩盖业务逻辑
            logger.exception("Redis 锁释放失败 key=%s", self._key)

    async def __aenter__(self) -> "RedisLock":
        # 简单阻塞重试：渠道回合/leader 选举都能接受短暂等待
        while not await self.acquire():
            await asyncio.sleep(0.05)
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        await self.release()


class LeaderLease:
    """常驻循环的 leader 租约：抢到才执行本轮；TTL 到期自动让位。

    hold() 是异步上下文管理器：进入时非阻塞抢锁，抢到则执行代码块并在结束时
    释放（让下一轮/其他 worker 能立即接管）；没抢到则整块跳过。
    """

    def __init__(self, name: str, *, ttl_ms: int = 60_000) -> None:
        self._lock = RedisLock(name, ttl_ms=ttl_ms)

    async def try_hold(self) -> bool:
        return await self._lock.acquire()

    async def release(self) -> None:
        await self._lock.release()

    @asynccontextmanager
    async def hold(self) -> AsyncIterator[bool]:
        held = await self.try_hold()
        try:
            yield held
        finally:
            if held:
                await self.release()


# ---- 停止信号广播 ----

_STOP_CHANNEL = "stop:signal"
_local_active: set[str] = set()
_local_stopped: set[str] = set()
_subscriber_task: asyncio.Task[None] | None = None


def _stop_key(task_id: str) -> str:
    return f"stop:{task_id}"


def _active_key(task_id: str) -> str:
    return f"stop:active:{task_id}"


async def begin(task_id: str) -> None:
    """任务开始：本地登记 + Redis 登记活跃 + 清停止标记（跨 worker 可见）。"""

    redis = get_redis()
    _local_active.add(task_id)
    _local_stopped.discard(task_id)
    await cast(Awaitable[int], redis.set(_active_key(task_id), "1", ex=3600))
    await cast(Awaitable[int], redis.delete(_stop_key(task_id)))


async def request_stop(task_id: str) -> bool:
    """请求停止：本地置位 + 写 Redis + 广播，返回是否确有进行中任务。

    活跃判定以 Redis 为准（本地只作兜底），这样 A worker 起的任务在 B worker
    点停止时也能识别为进行中并广播，A 的监听订阅收到后置本地停止标记。
    """

    redis = get_redis()
    active = task_id in _local_active or bool(
        await cast(Awaitable[int], redis.exists(_active_key(task_id)))
    )
    if not active:
        logger.info("停止请求被忽略：任务不存在或已结束 task_id=%s", task_id)
        return False
    _local_stopped.add(task_id)
    await cast(Awaitable[int], redis.set(_stop_key(task_id), "1", ex=3600))
    await cast(Awaitable[int], redis.publish(_STOP_CHANNEL, json.dumps({"task_id": task_id})))
    logger.info("收到停止请求 task_id=%s", task_id)
    return True


def is_stopped(task_id: str) -> bool:
    """同步判断（高频/ADK stop_check）：只读本地集合。"""

    return task_id in _local_stopped


def has_active(task_id: str) -> bool:
    """同步读（仅本地，热路径用）；跨 worker 权威判定走 request_stop 的 Redis 读。"""

    return task_id in _local_active


async def end(task_id: str) -> None:
    """任务结束：清本地与 Redis。"""

    redis = get_redis()
    _local_active.discard(task_id)
    _local_stopped.discard(task_id)
    await cast(Awaitable[int], redis.delete(_stop_key(task_id), _active_key(task_id)))


# ---- 共享布尔标志（Redis + PubSub 同步本地镜像，供同步热路径读） ----

_FLAG_CHANNEL = "flag:signal"
_FLAG_TTL = 3600
_local_flags: dict[str, bool] = {}
# 插话中断：外部注册回调，flag 名为 chat-interrupt:{session_id} 时触发本地 abort
_interrupt_hooks: list[Any] = []


def register_interrupt_hook(hook: Any) -> None:
    """注册跨 worker 插话中断回调 hook(session_id)；供编排层在启动时挂接。"""

    _interrupt_hooks.append(hook)


def _flag_key(name: str) -> str:
    return f"flag:{name}"


async def set_flag(name: str, value: bool) -> None:
    """设置共享标志：写 Redis + 广播，所有 worker 同步本地镜像。"""

    redis = get_redis()
    if value:
        await redis.set(_flag_key(name), "1", ex=_FLAG_TTL)
    else:
        await redis.delete(_flag_key(name))
    _local_flags[name] = value
    await redis.publish(_FLAG_CHANNEL, json.dumps({"name": name, "value": value}))


def get_flag(name: str) -> bool:
    """同步读（ADK stop_check 等热路径）：只读本地镜像。"""

    return _local_flags.get(name, False)


async def sync_flag(name: str) -> bool:
    """异步权威读：从 Redis 刷新本地镜像后返回（控制路径用）。"""

    value = bool(await cast(Awaitable[int], get_redis().exists(_flag_key(name))))
    _local_flags[name] = value
    return value


async def set_add(key: str, member: str, *, ttl: int = _FLAG_TTL) -> None:
    """共享集合追加成员（原子 SADD，避免多 worker 追加丢更新）。"""

    redis = get_redis()
    await cast(Awaitable[int], redis.sadd(key, member))
    await cast(Awaitable[int], redis.expire(key, ttl))


async def set_members(key: str) -> list[str]:
    return list(await cast(Awaitable[set[str]], get_redis().smembers(key)))


async def set_has(key: str, member: str) -> bool:
    return bool(await cast(Awaitable[int], get_redis().sismember(key, member)))


async def start_stop_listener() -> None:
    """订阅停止广播：其他 worker 的停止请求同步到本进程本地集合。"""

    global _subscriber_task
    if _subscriber_task is not None and not _subscriber_task.done():
        return
    _subscriber_task = asyncio.create_task(_listen_stops())
    logger.info("停止信号广播订阅已启动")


async def stop_stop_listener() -> None:
    global _subscriber_task
    if _subscriber_task is None:
        return
    _subscriber_task.cancel()
    try:
        await _subscriber_task
    except asyncio.CancelledError:
        pass
    _subscriber_task = None
    logger.info("停止信号广播订阅已停止")


async def _listen_stops() -> None:
    redis = get_redis()
    pubsub = redis.pubsub()
    await pubsub.subscribe(_STOP_CHANNEL, _FLAG_CHANNEL)
    try:
        async for message in pubsub.listen():
            if message.get("type") != "message":
                continue
            channel = message.get("channel")
            try:
                payload = json.loads(message["data"])
            except (json.JSONDecodeError, AttributeError, TypeError):
                continue
            if channel == _STOP_CHANNEL:
                task_id = payload.get("task_id")
                if task_id:
                    _local_stopped.add(task_id)
                    logger.info("收到跨 worker 停止信号 task_id=%s", task_id)
            elif channel == _FLAG_CHANNEL:
                name = payload.get("name")
                if name:
                    _local_flags[name] = bool(payload.get("value"))
                    logger.info("收到跨 worker 共享标志 name=%s value=%s", name, payload.get("value"))
                    # 插话中断：chat-interrupt:{session_id} 的置位触发本地回调
                    if name.startswith("chat-interrupt:") and payload.get("value"):
                        sid = name[len("chat-interrupt:"):]
                        for hook in list(_interrupt_hooks):
                            try:
                                hook(sid)
                            except Exception:  # noqa: BLE001 - 单个 hook 失败不影响广播循环
                                logger.exception("插话中断 hook 执行失败 sid=%s", sid)
    except asyncio.CancelledError:
        raise
    finally:
        await pubsub.aclose()
