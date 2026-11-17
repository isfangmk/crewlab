"""兼容按模块路径导入：redis 连接 API 实际定义在 app.redis 包。"""

from app.redis import close, connect, get_redis

__all__ = ["connect", "close", "get_redis"]
