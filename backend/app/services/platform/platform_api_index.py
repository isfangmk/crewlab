"""平台接口语义检索：启动时把接口目录批量嵌入到内存，查询时按余弦相似度排序。

与 MCP 工具检索的区别：平台接口目录固定（约 200 条）、生命周期随进程、且可由 OpenAPI
随时重建，因此向量放进程内内存即可，不必落 vector_store——避免元数据表与向量库漂移同步。
嵌入服务不可用时检索显式失败，不静默退化成关键词匹配。
"""

from __future__ import annotations

import asyncio
import logging
import math

from app.services.platform.api_catalog import PlatformApiEntry, build_api_catalog
from app.services.platform.embedding_client import embed_texts

logger = logging.getLogger(__name__)


def _cosine(a: list[float], b: list[float]) -> float:
    if len(a) != len(b):
        raise ValueError(f"向量维度不一致 a={len(a)} b={len(b)}")
    dot = na = nb = 0.0
    for x, y in zip(a, b, strict=True):
        dot += x * y
        na += x * x
        nb += y * y
    if na <= 0.0 or nb <= 0.0:
        return 0.0
    return dot / (math.sqrt(na) * math.sqrt(nb))


# 进程内索引；未就绪时为空，检索会显式报错（不静默降级）。
_entries: tuple[PlatformApiEntry, ...] = ()
_vectors: tuple[list[float], ...] = ()


async def warm_platform_api_index() -> int:
    """启动后台预热：批量嵌入接口目录。返回索引条数。"""
    global _entries, _vectors
    entries = tuple(build_api_catalog())
    texts = [e.search_text for e in entries]
    started = asyncio.get_running_loop().time()
    vectors = await embed_texts(texts)
    _entries, _vectors = entries, tuple(vectors)
    logger.info(
        "平台接口语义索引预热完成 apis=%s dim=%s 耗时=%.2fs",
        len(_entries),
        len(_vectors[0]) if _vectors else 0,
        asyncio.get_running_loop().time() - started,
    )
    return len(_entries)


async def search_platform_apis(query: str, *, limit: int = 5) -> list[dict]:
    """语义检索平台接口。

    返回候选及其参数清单——模型必须看到正确参数名才能构造 arguments，
    否则会凭猜测传错（如把 limit/offset 写成 page/page_size）。
    索引或查询向量失败时抛异常，由工具层转结构化错误，不 swallow。
    """
    if not isinstance(query, str) or query.strip() == "":
        raise ValueError("检索关键词不能为空")
    if not _entries:
        raise RuntimeError("平台接口索引尚未就绪，请稍后重试")
    if limit < 1:
        return []
    query_vec = (await embed_texts([query.strip()]))[0]
    ranked: list[tuple[int, float]] = []
    for index, vector in enumerate(_vectors):
        score = _cosine(query_vec, vector)
        ranked.append((index, score))
    ranked.sort(key=lambda item: (-item[1], _entries[item[0]].api_id))
    return [
        {
            "api_id": _entries[i].api_id,
            "method": _entries[i].method,
            "path": _entries[i].path,
            "summary": _entries[i].summary,
            "description": _entries[i].description,
            "risk": _entries[i].risk,
            "score": round(score, 4),
            "parameters": _entries[i].parameter_specs(),
        }
        for i, score in ranked[:limit]
    ]
