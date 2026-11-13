"""向量存储抽象层：与业务数据库分离，按配置切换实现。

契约（所有实现必须一致）：
  - 向量按 (namespace, ref_id) 二元组寻址，ref_id 是字符串键
  - search 返回按相似度降序的 (ref_id, similarity)，similarity 是余弦相似度，
    高者胜；调用方自行用业务库把 ref_id join 回业务行并做属性过滤
  - 向量库不做兜底：维度不符、实现不支持时显式失败

实现：
  - pgvector：复用业务 PostgreSQL 库建 vector_items 向量表
  - sqlite：独立 SQLite 向量文件（VECTOR_SQLITE_PATH），应用侧余弦检索，
    适合无 PG 的开发环境与中小规模
  - 后续接专用向量库（Milvus / Qdrant 等）时按本协议新增实现即可

选择由 VECTOR_BACKEND 控制：
  - auto    = 按 DATABASE_URL 推导（PG → pgvector，SQLite → sqlite 向量文件）
  - pgvector= 用业务 PG 库存向量（要求 DATABASE_URL 是 postgresql）
  - sqlite  = 用独立 SQLite 向量文件
  - none    = 显式停用：依赖向量的操作（知识库切块、语义检索）全部显式失败
"""

from __future__ import annotations

import logging
import math
from typing import Protocol, runtime_checkable

from app.config import get_settings

logger = logging.getLogger(__name__)

# 知识库切块向量；ref_id = str(KnowledgeChunk.id)
KNOWLEDGE_NAMESPACE = "knowledge_chunks"
# MCP 工具向量；ref_id = 工具 wire 名（mcp__{server}__{tool}，全局唯一）
MCP_TOOL_NAMESPACE = "mcp_tools"


class VectorStoreUnavailable(RuntimeError):
    """向量存储未启用/不可达：环境问题，不是调用方的契约错误。

    与 RuntimeError 分型是为了 HTTP 映射：业务参数错误 400，
    环境不可用 503，前端与测试据此区分「请求写错了」和「服务端没配好」。
    """


@runtime_checkable
class VectorStore(Protocol):
    """向量存储实现契约；全部异步。"""

    async def ensure_ready(self, dim: int) -> None:
        """建表/校验实现可用性；启动时调用一次。"""

    async def upsert_many(
        self, namespace: str, items: list[tuple[str, list[float]]]
    ) -> None:
        """批量写入或覆盖向量；items = [(ref_id, vector), ...]。"""

    async def delete_refs(self, namespace: str, ref_ids: list[str]) -> None:
        """删除指定 ref_id 的向量；不存在的 ref 静默跳过。"""

    async def search(
        self, namespace: str, query_vector: list[float], limit: int
    ) -> list[tuple[str, float]]:
        """按余弦相似度降序返回 (ref_id, similarity)；只搜指定 namespace。"""

    async def fetch_vectors(
        self, namespace: str, ref_ids: list[str]
    ) -> dict[str, list[float]]:
        """按 ref_id 批量取向量；缺失的 ref 不出现在结果里。"""

    async def list_vectors(self, namespace: str) -> dict[str, list[float]]:
        """返回 namespace 下全部向量（ref_id → vector）。"""


def cosine_similarity(a: list[float], b: list[float]) -> float:
    """纯 Python 余弦相似度；维度不符显式失败（无索引实现共用）。"""

    if len(a) != len(b):
        raise ValueError(f"向量维度不一致 a={len(a)} b={len(b)}")
    dot = 0.0
    na = 0.0
    nb = 0.0
    for x, y in zip(a, b, strict=True):
        dot += x * y
        na += x * x
        nb += y * y
    if na <= 0.0 or nb <= 0.0:
        return 0.0
    return dot / (math.sqrt(na) * math.sqrt(nb))


def raise_vector_store_unavailable() -> None:
    """向量存储未启用时的统一显式失败入口。"""

    raise VectorStoreUnavailable(
        "向量存储不可用：当前配置下没有可用的 VECTOR_BACKEND 实现；"
        "向量依赖功能（知识库切块/语义检索、MCP 工具语义索引）已停用"
    )


def build_vector_store() -> VectorStore | None:
    """按 VECTOR_BACKEND 配置构建实现；非法取值显式失败。"""

    settings = get_settings()
    backend = settings.vector_backend
    if backend == "auto":
        # 按 URL 协议推导；未知协议显式报错，而不是错推成某个实现
        if settings.database_url.startswith("postgresql"):
            backend = "pgvector"
        elif settings.database_url.startswith("sqlite"):
            backend = "sqlite"
        else:
            raise ValueError(
                f"无法按 DATABASE_URL 推导向量后端: {settings.database_url}；"
                "请显式配置 VECTOR_BACKEND"
            )
    if backend == "none":
        logger.warning("向量存储已停用 VECTOR_BACKEND=none")
        return None
    if backend == "pgvector":
        if not settings.database_url.startswith("postgresql"):
            raise ValueError(
                "VECTOR_BACKEND=pgvector 要求 DATABASE_URL 是 postgresql，"
                f"当前: {settings.database_url}"
            )
        from app.vector_store.pgvector_backend import PgVectorStore

        return PgVectorStore()
    if backend == "sqlite":
        from app.vector_store.sqlite_backend import SqliteVectorStore

        return SqliteVectorStore(settings.vector_sqlite_path)
    raise ValueError(f"VECTOR_BACKEND 取值无效: {settings.vector_backend}")


_instance: VectorStore | None = None
_built = False


def get_vector_store() -> VectorStore | None:
    """进程内单例；未启用时返回 None（调用方显式失败，禁止静默降级）。"""

    global _instance, _built
    if not _built:
        _instance = build_vector_store()
        _built = True
    return _instance
