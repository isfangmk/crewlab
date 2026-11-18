"""pgvector 向量存储：复用业务 PostgreSQL 库，向量检索下推数据库。

vector_items 是向量库自有表，与业务表分离；pgvector 扩展由应用启动时创建。
维度不建固定 vector(N)：每行带 dim 列，配置改维度时旧数据能被显式检出。
"""

from __future__ import annotations

import logging
from collections.abc import Iterable
from typing import cast

from sqlalchemy import text

from app.db import engine
from app.vector_store import VectorStore

logger = logging.getLogger(__name__)

_DDL = text(
    """
    CREATE TABLE IF NOT EXISTS vector_items (
      namespace  TEXT        NOT NULL,
      ref_id     TEXT        NOT NULL,
      dim        INTEGER     NOT NULL,
      embedding  vector      NOT NULL,
      updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
      PRIMARY KEY (namespace, ref_id)
    )
    """
)


def to_pg_vector(vector: list[float]) -> str:
    """pgvector 文本字面量：'[1.0,2.0,…]'，SQL 侧 ::vector 转换。"""

    return "[" + ",".join(repr(float(x)) for x in vector) + "]"


def from_pg_vector(embedding: object) -> list[float]:
    """把 pgvector 列值还原成浮点数组。

    psycopg 把 vector 列读成文本字面量 '[0.1,0.2,…]'（个别驱动返回 list/tuple），
    两种形态都要能解析——不能按字符迭代逐个 float，否则第一个 '[' 就报错。
    """

    if isinstance(embedding, str):
        return [float(x) for x in embedding.strip().strip("[]").split(",") if x.strip()]
    return [float(x) for x in cast(Iterable[float | int | str], embedding)]


class PgVectorStore(VectorStore):
    """用业务库引擎访问 pgvector；不另建连接池。"""

    async def ensure_ready(self, dim: int) -> None:
        """建向量表；dim 由每行携带（配置改维度时靠 search 的维度校验显式失败）。"""

        async with engine.begin() as conn:
            await conn.execute(_DDL)
        logger.info("向量存储(pgvector)表已就绪 dim=%s", dim)

    async def upsert_many(
        self, namespace: str, items: list[tuple[str, list[float]]]
    ) -> None:
        if not items:
            return
        async with engine.begin() as conn:
            await conn.execute(
                text(
                    """
                    INSERT INTO vector_items (namespace, ref_id, dim, embedding)
                    VALUES (:ns, :ref, :dim, CAST(:vec AS vector))
                    ON CONFLICT (namespace, ref_id) DO UPDATE SET
                      dim = excluded.dim,
                      embedding = excluded.embedding,
                      updated_at = now()
                    """
                ),
                [
                    {
                        "ns": namespace,
                        "ref": ref_id,
                        "dim": len(vec),
                        "vec": to_pg_vector(vec),
                    }
                    for ref_id, vec in items
                ],
            )

    async def delete_refs(self, namespace: str, ref_ids: list[str]) -> None:
        if not ref_ids:
            return
        async with engine.begin() as conn:
            await conn.execute(
                text(
                    "DELETE FROM vector_items WHERE namespace = :ns "
                    "AND ref_id = ANY(:refs)"
                ),
                {"ns": namespace, "refs": list(ref_ids)},
            )

    async def search(
        self, namespace: str, query_vector: list[float], limit: int
    ) -> list[tuple[str, float]]:
        if limit < 1:
            return []
        async with engine.connect() as conn:
            # 维度漂移必须显式失败且先于检索：检索 SQL 里异维向量会让
            # <=> 直接抛 PG 内部错误，报错信息无法定位到「改过配置」。
            stale = await conn.execute(
                text(
                    "SELECT count(*) FROM vector_items "
                    "WHERE namespace = :ns AND dim <> :dim"
                ),
                {"ns": namespace, "dim": len(query_vector)},
            )
            if (stale.scalar_one() or 0) > 0:
                raise RuntimeError(
                    f"向量维度与配置不一致 namespace={namespace} "
                    f"query_dim={len(query_vector)}；请重建向量索引"
                )
            rows = (
                await conn.execute(
                    text(
                        """
                        SELECT ref_id, 1 - (embedding <=> CAST(:q AS vector)) AS similarity
                        FROM vector_items
                        WHERE namespace = :ns AND dim = :dim
                        ORDER BY embedding <=> CAST(:q AS vector)
                        LIMIT :k
                        """
                    ),
                    {
                        "ns": namespace,
                        "dim": len(query_vector),
                        "q": to_pg_vector(query_vector),
                        "k": limit,
                    },
                )
            ).all()
        return [(str(ref_id), float(similarity)) for ref_id, similarity in rows]

    async def fetch_vectors(
        self, namespace: str, ref_ids: list[str]
    ) -> dict[str, list[float]]:
        if not ref_ids:
            return {}
        async with engine.connect() as conn:
            rows = (
                await conn.execute(
                    text(
                        "SELECT ref_id, embedding FROM vector_items "
                        "WHERE namespace = :ns AND ref_id = ANY(:refs)"
                    ),
                    {"ns": namespace, "refs": list(ref_ids)},
                )
            ).all()
        return {str(ref_id): from_pg_vector(embedding) for ref_id, embedding in rows}

    async def list_vectors(self, namespace: str) -> dict[str, list[float]]:
        async with engine.connect() as conn:
            rows = (
                await conn.execute(
                    text(
                        "SELECT ref_id, embedding FROM vector_items "
                        "WHERE namespace = :ns"
                    ),
                    {"ns": namespace},
                )
            ).all()
        return {str(ref_id): from_pg_vector(embedding) for ref_id, embedding in rows}
