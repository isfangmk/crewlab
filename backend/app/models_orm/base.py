"""ORM 基类与跨方言类型。"""

from datetime import timezone

from sqlalchemy import DateTime, types
from sqlalchemy.orm import DeclarativeBase


class Base(DeclarativeBase):
    """所有业务表的声明基类。"""


class AwareDateTime(types.TypeDecorator):
    """跨方言 DateTime(timezone=True)。

    PG(asyncpg) 读回带时区的 datetime，SQLite 读回 naive；
    统一在读取时补 UTC，业务代码始终拿到 aware 值，混比不再抛 TypeError。
    写入口径不变：SQLite 落 UTC 墙钟时间，func.now() 落 CURRENT_TIMESTAMP（UTC）。
    """

    impl = DateTime(timezone=True)
    cache_ok = True

    def process_result_value(self, value, dialect):
        if value is not None and value.tzinfo is None:
            return value.replace(tzinfo=timezone.utc)
        return value
