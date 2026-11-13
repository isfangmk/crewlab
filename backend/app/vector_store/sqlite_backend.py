"""SQLite 向量存储：独立文件 + 应用侧余弦检索。

向量按 float32 紧凑存 BLOB（4096 维 ≈ 16 KB/条）；没有向量索引，
检索是 namespace 内全量扫描后逐条算余弦，只适合几千条以内的中小规模。
生产口径仍是 pgvector（或后续专用向量库），本实现用于无 PG 的开发环境。
"""

from __future__ import annotations

import logging
from array import array
from datetime import datetime, timezone
from pathlib import Path

from sqlalchemy import bindparam, text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.vector_store import VectorStore, cosine_similarity

logger = logging.getLogger(__name__)

_DDL = text(
    """
    CREATE TABLE IF NOT EXISTS vector_items (
      namespace  TEXT    NOT NULL,
      ref_id     TEXT    NOT NULL,
      dim        INTEGER NOT NULL,
      vector     BLOB    NOT NULL,
      updated_at TEXT    NOT NULL,
      PRIMARY KEY (namespace, ref_id)
    )
    """
)

# IN 列表参数必须声明 expanding，text() 才会把列表展开成占位符
_DELETE_SQL = text(
    "DELETE FROM vector_items WHERE namespace = :ns AND ref_id IN :refs"
).bindparams(bindparam("ns"), bindparam("refs", expanding=True))

_FETCH_SQL = text(
    "SELECT ref_id, vector FROM vector_items "
    "WHERE namespace = :ns AND ref_id IN :refs"
).bindparams(bindparam("ns"), bindparam("refs", expanding=True))


def encode_vector(vector: list[float]) -> bytes:
    """float32 二进制编码；精度损失对余弦相似度可忽略。"""

    return array("f", vector).tobytes()


def decode_vector(blob: bytes) -> list[float]:
    """float32 二进制还原为浮点列表。"""

    out = array("f")
    out.frombytes(bytes(blob))
    return out.tolist()


class SqliteVectorStore(VectorStore):
    """独立 SQLite 文件实现；与业务库物理隔离，便于单独备份/迁移。"""

    def __init__(self, path: str) -> None:
        path_obj = Path(path)
        path_obj.parent.mkdir(parents=True, exist_ok=True)
        self._engine = create_async_engine(
            f"sqlite+aiosqlite:///{path_obj.as_posix()}",
            connect_args={"timeout": 30},
        )
        self._session = async_sessionmaker(self._engine, expire_on_commit=False)
        logger.info("向量存储(SQLite)已配置 path=%s", path_obj)

    async def ensure_ready(self, dim: int) -> None:
        async with self._engine.begin() as conn:
            await conn.execute(_DDL)
        logger.info("向量存储(SQLite)表已就绪 dim=%s", dim)

    async def upsert_many(
        self, namespace: str, items: list[tuple[str, list[float]]]
    ) -> None:
        if not items:
            return
        now = datetime.now(timezone.utc).isoformat()
        async with self._session() as session:
            await session.execute(
                text(
                    """
                    INSERT INTO vector_items (namespace, ref_id, dim, vector, updated_at)
                    VALUES (:ns, :ref, :dim, :vec, :ts)
                    ON CONFLICT(namespace, ref_id) DO UPDATE SET
                      dim = excluded.dim,
                      vector = excluded.vector,
                      updated_at = excluded.updated_at
                    """
                ),
                [
                    {
                        "ns": namespace,
                        "ref": ref_id,
                        "dim": len(vec),
                        "vec": encode_vector(vec),
                        "ts": now,
                    }
                    for ref_id, vec in items
                ],
            )
            await session.commit()

    async def delete_refs(self, namespace: str, ref_ids: list[str]) -> None:
        if not ref_ids:
            return
        async with self._session() as session:
            await session.execute(_DELETE_SQL, {"ns": namespace, "refs": list(ref_ids)})
            await session.commit()

    async def search(
        self, namespace: str, query_vector: list[float], limit: int
    ) -> list[tuple[str, float]]:
        if limit < 1:
            return []
        async with self._session() as session:
            rows = (
                await session.execute(
                    text(
                        "SELECT ref_id, dim, vector FROM vector_items "
                        "WHERE namespace = :ns"
                    ),
                    {"ns": namespace},
                )
            ).all()
        hits: list[tuple[str, float]] = []
        for ref_id, dim, blob in rows:
            if int(dim) != len(query_vector):
                raise RuntimeError(
                    f"向量维度与查询不一致 ref={ref_id} stored={dim} query={len(query_vector)}"
                )
            hits.append(
                (str(ref_id), cosine_similarity(query_vector, decode_vector(blob)))
            )
        hits.sort(key=lambda item: (-item[1], item[0]))
        return hits[:limit]

    async def fetch_vectors(
        self, namespace: str, ref_ids: list[str]
    ) -> dict[str, list[float]]:
        if not ref_ids:
            return {}
        async with self._session() as session:
            rows = (
                await session.execute(
                    _FETCH_SQL, {"ns": namespace, "refs": list(ref_ids)}
                )
            ).all()
        return {str(ref_id): decode_vector(blob) for ref_id, blob in rows}

    async def list_vectors(self, namespace: str) -> dict[str, list[float]]:
        async with self._session() as session:
            rows = (
                await session.execute(
                    text("SELECT ref_id, vector FROM vector_items WHERE namespace = :ns"),
                    {"ns": namespace},
                )
            ).all()
        return {str(ref_id): decode_vector(blob) for ref_id, blob in rows}
