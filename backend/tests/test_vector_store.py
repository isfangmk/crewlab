"""向量存储抽象层：SQLite 实现全协议 + 工厂分支（真实临时文件，无 mock）。"""

from __future__ import annotations

import pytest

from app.config import get_settings
from app.vector_store import (
    VectorStoreUnavailable,
    build_vector_store,
    cosine_similarity,
    raise_vector_store_unavailable,
)
from app.vector_store.pgvector_backend import to_pg_vector
from app.vector_store.sqlite_backend import (
    SqliteVectorStore,
    decode_vector,
    encode_vector,
)


def test_cosine_similarity() -> None:
    """同向为 1、正交为 0、零向量按 0 处理、维度不符显式失败。"""

    assert cosine_similarity([1.0, 0.0], [1.0, 0.0]) == pytest.approx(1.0)
    assert cosine_similarity([1.0, 0.0], [0.0, 1.0]) == pytest.approx(0.0)
    assert cosine_similarity([0.0, 0.0], [1.0, 0.0]) == 0.0
    with pytest.raises(ValueError, match="维度不一致"):
        cosine_similarity([1.0], [1.0, 0.0])


def test_vector_blob_roundtrip() -> None:
    """float32 编解码还原同一组值。"""

    vec = [0.25, -1.5, 3.75]
    assert decode_vector(encode_vector(vec)) == pytest.approx(vec)


def test_to_pg_vector_literal() -> None:
    """pgvector 文本字面量保持浮点精度标记。"""

    assert to_pg_vector([1.0, 0.5]) == "[1.0,0.5]"


async def test_sqlite_store_full_protocol(tmp_path) -> None:
    """真实临时文件上走完整契约：写入/覆盖/检索/隔离/删除/维度校验。"""

    store = SqliteVectorStore(str(tmp_path / "vectors.db"))
    await store.ensure_ready(dim=4)

    await store.upsert_many(
        "ns1",
        [("a", [1.0, 0.0, 0.0, 0.0]), ("b", [0.0, 1.0, 0.0, 0.0]), ("c", [0.9, 0.1, 0.0, 0.0])],
    )
    # 相似度降序；同分按 ref_id 稳定排序
    hits = await store.search("ns1", [1.0, 0.0, 0.0, 0.0], limit=3)
    assert [ref for ref, _ in hits] == ["a", "c", "b"]
    assert hits[0][1] == pytest.approx(1.0)

    # namespace 之间互相不可见
    assert await store.search("ns2", [1.0, 0.0, 0.0, 0.0], limit=5) == []

    # 覆盖写：同 ref 更新向量，不产生第二行
    await store.upsert_many("ns1", [("a", [0.0, 1.0, 0.0, 0.0])])
    hits = await store.search("ns1", [0.0, 1.0, 0.0, 0.0], limit=2)
    assert [ref for ref, _ in hits] == ["a", "b"]

    # 按引用批量取；缺失的 ref 不出现
    got = await store.fetch_vectors("ns1", ["a", "missing"])
    assert set(got) == {"a"}
    assert len(got["a"]) == 4

    # 全量列出（warm 缓存路径）
    everything = await store.list_vectors("ns1")
    assert set(everything) == {"a", "b", "c"}

    # 维度不符显式失败，不做静默跳过
    with pytest.raises(RuntimeError, match="维度与查询不一致"):
        await store.search("ns1", [1.0, 0.0], limit=1)

    # 删除后不再命中
    await store.delete_refs("ns1", ["a"])
    assert [ref for ref, _ in await store.search("ns1", [0.0, 1.0, 0.0, 0.0], limit=5)] == ["b", "c"]
    # 删除不存在的 ref 静默跳过（契约行为）
    await store.delete_refs("ns1", ["missing"])


def test_build_vector_store_branches(monkeypatch, tmp_path) -> None:
    """真实配置加载下验证各分支：auto 推导 / none / 显式实现 / 非法值显式失败。

    get_settings 是 lru_cache：每次改环境变量后都必须 cache_clear，
    否则读到的还是上一分支的缓存配置。
    """

    from app.vector_store.pgvector_backend import PgVectorStore

    monkeypatch.setenv("KNOWLEDGE_UPLOAD_DIR", str(tmp_path))
    monkeypatch.setenv("VECTOR_BACKEND", "pgvector")
    monkeypatch.setenv("DATABASE_URL", "sqlite+aiosqlite:///./t.db")
    get_settings.cache_clear()
    try:
        # pgvector 要求 PG 业务库：配错即显式失败
        with pytest.raises(ValueError, match="postgresql"):
            build_vector_store()

        monkeypatch.setenv("VECTOR_BACKEND", "none")
        get_settings.cache_clear()
        assert build_vector_store() is None

        monkeypatch.setenv("VECTOR_BACKEND", "非法值")
        get_settings.cache_clear()
        with pytest.raises(ValueError, match="取值无效"):
            build_vector_store()

        monkeypatch.setenv("VECTOR_BACKEND", "auto")
        monkeypatch.setenv("DATABASE_URL", "postgresql+asyncpg://aiwb:aiwb@127.0.0.1:1/db")
        get_settings.cache_clear()
        assert isinstance(build_vector_store(), PgVectorStore)

        monkeypatch.setenv("DATABASE_URL", "sqlite+aiosqlite:///./t.db")
        get_settings.cache_clear()
        assert isinstance(build_vector_store(), SqliteVectorStore)

        # auto 遇到未知协议显式失败，不猜测实现
        monkeypatch.setenv("DATABASE_URL", "mysql://x/y")
        get_settings.cache_clear()
        with pytest.raises(ValueError, match="无法按 DATABASE_URL 推导"):
            build_vector_store()

        with pytest.raises(VectorStoreUnavailable):
            raise_vector_store_unavailable()
    finally:
        # monkeypatch 恢复环境变量后必须清缓存，否则后续用例读到污染配置
        get_settings.cache_clear()
