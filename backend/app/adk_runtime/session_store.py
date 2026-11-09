"""ADK SessionService 单例：DatabaseSessionService 持久化多轮事件。"""

from __future__ import annotations

import logging

from google.adk.sessions import BaseSessionService, DatabaseSessionService

logger = logging.getLogger(__name__)

_adk_sessions: DatabaseSessionService | None = None


def get_adk_session_service() -> BaseSessionService:
    """返回进程内唯一的 ADK DatabaseSessionService。

    复用业务库的 engine：由 db_url 自行建引擎会让连接池翻倍，
    且进程退出时无法随业务引擎一起释放。
    """

    global _adk_sessions
    if _adk_sessions is None:
        from app.db import engine

        _adk_sessions = DatabaseSessionService(db_engine=engine)
        logger.info("已创建 ADK DatabaseSessionService（复用业务库 engine）")
    return _adk_sessions


async def prepare_adk_session_tables() -> None:
    """启动时预建 ADK sessions/events 表。"""

    service = get_adk_session_service()
    if not isinstance(service, DatabaseSessionService):
        raise TypeError("ADK SessionService 必须是 DatabaseSessionService")
    await service.prepare_tables()
    logger.info("ADK DatabaseSession 表已就绪")
