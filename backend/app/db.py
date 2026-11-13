"""数据库引擎与会话工厂。

业务数据库支持两种实现，由 DATABASE_URL 决定：
  - postgresql+asyncpg://…  原生 PG（生产口径）
  - sqlite+aiosqlite:///…   本地 SQLite（开发/无中间件环境）

向量存储不在这里：见 app.vector_store，与业务库分离、独立配置。
"""

from __future__ import annotations

from collections.abc import AsyncGenerator
from pathlib import Path

from sqlalchemy import event
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from app.config import get_settings


def _tune_sqlite_sync_engine(engine: AsyncEngine) -> AsyncEngine:
    """SQLite 连接级 PRAGMA：FK 级联、写锁等待、WAL。

    ORM 里 passive_deletes=True 依赖数据库侧 CASCADE，
    SQLite 默认不开启外键约束，必须逐连接打开。
    """

    @event.listens_for(engine.sync_engine, "connect")
    def _sqlite_pragmas(dbapi_conn, _record) -> None:
        cursor = dbapi_conn.cursor()
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.execute("PRAGMA busy_timeout=30000")
        cursor.execute("PRAGMA journal_mode=WAL")
        cursor.close()

    return engine


def _ensure_sqlite_parent_dir(database_url: str) -> None:
    """SQLite 文件的父目录不存在时先建好；sqlite3 不会自动创建多级目录。"""

    if "?" in database_url:
        database_url = database_url.split("?", 1)[0]
    marker = ":///"
    if marker not in database_url:
        raise ValueError(f"DATABASE_URL 缺少文件路径: {database_url}")
    path = database_url.split(marker, 1)[1]
    if path in ("", ":memory:"):
        return
    Path(path).parent.mkdir(parents=True, exist_ok=True)


def create_db_engine(database_url: str, **engine_kwargs) -> AsyncEngine:
    """按 URL 协议建异步引擎；未知协议显式失败。

    SQLite 的连接级 PRAGMA（FK / busy_timeout / WAL）在这里统一打上，
    所有调用方（业务、测试、脚本）都必须经过本工厂：
    自建引擎会丢掉 FK 级联，让 passive_deletes=True 依赖的
    数据库侧 CASCADE 失效，删除主行后留下孤儿子行。
    """

    if database_url.startswith("sqlite"):
        _ensure_sqlite_parent_dir(database_url)
        # busy_timeout 由连接 PRAGMA 统一设置，不再用 sqlite3.connect
        # 的 timeout 参数（同一机制，两处配置会漂移）
        kwargs: dict = {"pool_pre_ping": True}
        kwargs.update(engine_kwargs)
        engine = create_async_engine(database_url, **kwargs)
        return _tune_sqlite_sync_engine(engine)
    if database_url.startswith("postgresql"):
        kwargs = {"pool_pre_ping": True}
        kwargs.update(engine_kwargs)
        return create_async_engine(database_url, **kwargs)
    raise ValueError(f"不支持的 DATABASE_URL: {database_url}")


_settings = get_settings()
engine = create_db_engine(_settings.database_url)
SessionLocal = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)


async def get_db() -> AsyncGenerator[AsyncSession, None]:
    """FastAPI 依赖：请求级 AsyncSession。"""

    async with SessionLocal() as session:
        yield session
