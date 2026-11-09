"""MCP / 工具目录语义检索：用 br-embedding 向量 + 余弦相似度，替代 BM25 关键词。

adk_tool_search 的 SearchableToolset 默认 BM25，且英文分词，对中文工具说明几乎无效。
本索引实现与 BM25ToolIndex 相同的 rebuild/search 契约，供 index_factory 注入。

MCP 增删改 / 探测成功后，工具向量同步写入 pgvector（mcp_tool_embeddings）。
"""

from __future__ import annotations

import hashlib
import logging
import math
from typing import Any

from adk_tool_search import ToolSearchResult
from adk_tool_search.toolset import SearchableToolset
from google.adk.tools.tool_context import ToolContext
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models_orm.mcp import McpServer, McpToolEmbedding
from app.services.embedding_client import embed_texts

logger = logging.getLogger(__name__)

# 进程内缓存：同一工具不重复打嵌入服务。
# 键为工具 wire 名（mcp__{server}__{tool}），值为 2560 维向量。
# 必须用工具名而不是 search_text 的哈希做键：search_text 由
# 名称 + 描述 + 参数说明拼成，任何一段措辞变动都会换哈希，
# 导致运行时索引与已入库向量对不上（工具检索整体失效）。
# 缓存必须有上限，否则进程长期运行内存只增不减。
_VECTOR_CACHE: dict[str, list[float]] = {}
_VECTOR_CACHE_MAX_ENTRIES = 4096

# 进程内缓存：查询向量，键为查询文本哈希。
# 与工具向量缓存必须分开：两者键空间不同（工具用 wire 名，查询用文本哈希），
# 混用会导致查询哈希撞上工具名而取到错误的向量。
_QUERY_VECTOR_CACHE: dict[str, list[float]] = {}
_QUERY_VECTOR_CACHE_MAX_ENTRIES = 512


def content_hash(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def build_tool_search_text(name: str, description: str, input_schema: dict) -> str:
    """与 adk_tool_search.ToolEntry.search_text 对齐，保证入库向量与运行时索引一致。"""

    if not isinstance(name, str) or name == "":
        raise ValueError("tool name 无效")

    properties = input_schema.get("properties")
    param_parts: list[str] = []
    if properties is not None:
        if not isinstance(properties, dict):
            raise TypeError("input_schema.properties 必须是对象")
        for pname, prop in properties.items():
            if not isinstance(pname, str) or pname == "":
                raise ValueError("input_schema.properties 键无效")
            if not isinstance(prop, dict):
                raise TypeError(f"properties[{pname}] 必须是对象")
            pdesc = prop.get("description", "")
            if pdesc is None:
                pdesc = ""
            if not isinstance(pdesc, str):
                raise TypeError(f"properties[{pname}].description 必须是字符串")
            param_parts.append(f"{pname} {pdesc}".strip())
    parameter_text = " ".join(param_parts)
    return " ".join(part for part in (name, description, parameter_text) if part)


def remember_vector(tool_name: str, vector: list[float]) -> None:
    """按工具 wire 名写入进程缓存，供 EmbeddingToolIndex 命中。

    超过上限时按插入顺序淘汰最旧的条目：缓存必须有界。
    """

    if tool_name not in _VECTOR_CACHE and len(_VECTOR_CACHE) >= _VECTOR_CACHE_MAX_ENTRIES:
        oldest = next(iter(_VECTOR_CACHE))
        del _VECTOR_CACHE[oldest]
    _VECTOR_CACHE[tool_name] = vector


async def forget_server_vectors(db: AsyncSession, server_id: int) -> int:
    """删除某 server 的工具向量，并同步清掉进程缓存里的对应条目。

    删除 server 后缓存不失效会让已删工具继续参与检索。
    """

    rows = await db.scalars(
        select(McpToolEmbedding).where(McpToolEmbedding.server_id == server_id)
    )
    count = 0
    for row in rows.all():
        if _VECTOR_CACHE.pop(row.tool_name, None) is not None:
            count += 1
    logger.info("已清理 MCP 工具向量缓存 server_id=%s entries=%s", server_id, count)
    return count


async def sync_server_tool_embeddings(
    db: AsyncSession,
    server_id: int,
    tools: list[dict],
) -> int:
    """按探测结果全量替换该 MCP Server 的工具向量；返回写入条数。"""

    if server_id < 1:
        raise ValueError("server_id 无效")

    rows_to_write: list[tuple[str, str, str]] = []
    for i, tool in enumerate(tools):
        if not isinstance(tool, dict):
            raise TypeError(f"tools[{i}] 必须是对象")
        name = tool.get("name")
        description = tool.get("description")
        schema = tool.get("input_schema")
        if not isinstance(name, str) or name == "":
            raise ValueError(f"tools[{i}].name 无效")
        if not isinstance(description, str):
            raise TypeError(f"tools[{i}].description 必须是字符串")
        if not isinstance(schema, dict):
            raise TypeError(f"tools[{i}].input_schema 必须是对象")
        text = build_tool_search_text(name, description, schema)
        rows_to_write.append((name, text, content_hash(text)))

    if not rows_to_write:
        await db.execute(
            delete(McpToolEmbedding).where(McpToolEmbedding.server_id == server_id)
        )
        await db.commit()
        logger.info("MCP 工具向量已清空 server_id=%s tools=0", server_id)
        return 0

    # 删除前先按 content_hash 复用已有向量，避免同文重复嵌入
    hashes = [h for _, _, h in rows_to_write]
    existing = await db.scalars(
        select(McpToolEmbedding).where(McpToolEmbedding.content_hash.in_(hashes))
    )
    hash_to_vec: dict[str, list[float]] = {}
    for row in existing.all():
        vec = list(row.embedding)
        hash_to_vec[row.content_hash] = vec
        remember_vector(row.tool_name, vec)

    missing_texts: list[str] = []
    missing_hashes: list[str] = []
    missing_names: list[str] = []
    seen_missing: set[str] = set()
    for name, text, h in rows_to_write:
        if h in hash_to_vec:
            continue
        cached = _VECTOR_CACHE.get(name)
        if cached is not None:
            hash_to_vec[h] = cached
            continue
        if h in seen_missing:
            continue
        seen_missing.add(h)
        missing_texts.append(text)
        missing_hashes.append(h)
        missing_names.append(name)

    if missing_texts:
        fresh = await embed_texts(missing_texts)
        for name, text, h, vec in zip(
            missing_names, missing_texts, missing_hashes, fresh, strict=True
        ):
            hash_to_vec[h] = vec
            remember_vector(name, vec)

    await db.execute(
        delete(McpToolEmbedding).where(McpToolEmbedding.server_id == server_id)
    )
    for name, text, h in rows_to_write:
        vec = hash_to_vec.get(h)
        if vec is None:
            raise RuntimeError(f"工具向量缺失 tool={name} hash={h}")
        db.add(
            McpToolEmbedding(
                server_id=server_id,
                tool_name=name,
                search_text=text,
                content_hash=h,
                embedding=vec,
            )
        )
        remember_vector(name, vec)

    await db.commit()
    logger.info(
        "MCP 工具向量已同步 server_id=%s tools=%s embedded_new=%s",
        server_id,
        len(rows_to_write),
        len(missing_texts),
    )
    return len(rows_to_write)


async def warm_vector_cache_from_db(db: AsyncSession) -> int:
    """对话装配前把已同步的工具向量灌进进程缓存。

    缓存键是工具 wire 名（与运行时索引一致），
    不是 search_text 的哈希。
    """

    rows = await db.scalars(select(McpToolEmbedding))
    count = 0
    for row in rows.all():
        remember_vector(row.tool_name, list(row.embedding))
        count += 1
    if count:
        logger.info("已从向量库预热工具缓存 count=%s", count)
    return count


async def assert_embedding_names_consistent(db: AsyncSession) -> None:
    """校验向量表 tool_name 与 mcp_tool_wire_name(server.name, tool) 一致。

    不一致说明向量数据写错了，必须显式失败，不能带着错误名字做检索。
    """

    from app.services.mcp_service import (
        mcp_server_name_from_wire_name,
        mcp_tool_wire_name,
    )

    rows = (await db.scalars(select(McpToolEmbedding))).all()
    if not rows:
        return
    server_ids = {row.server_id for row in rows}
    servers = {
        s.id: s
        for s in (
            await db.scalars(
                select(McpServer).where(McpServer.id.in_(server_ids))
            )
        ).all()
    }
    mismatched: list[str] = []
    for row in rows:
        server = servers.get(row.server_id)
        if server is None:
            mismatched.append(f"{row.tool_name}（server_id={row.server_id} 不存在）")
            continue
        try:
            wire_server = mcp_server_name_from_wire_name(row.tool_name)
            tool_seg = row.tool_name.split("__", 2)[2]
        except (ValueError, IndexError) as exc:
            mismatched.append(f"{row.tool_name}（无法解析: {exc}）")
            continue
        expected = mcp_tool_wire_name(server.name, tool_seg)
        if wire_server != server.name or expected != row.tool_name:
            mismatched.append(f"{row.tool_name} → 应为 {expected}")
    if mismatched:
        raise RuntimeError(
            "MCP 工具向量命名与 wire 规则不一致：" + "; ".join(mismatched[:10])
        )
    logger.info("MCP 工具向量命名校验通过 count=%s", len(rows))


def _cosine(a: list[float], b: list[float]) -> float:
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



async def prefetch_query_vector(query: str) -> list[float]:
    """异步预取查询向量。

    检索路径必须在事件循环里做一次嵌入调用；放在这里用异步客户端完成，
    不会阻塞事件循环（adk_tool_search 的 index.search 是同步契约，
    因此查询向量必须提前算好再传进去）。
    """

    key = content_hash(query)
    cached = _QUERY_VECTOR_CACHE.get(key)
    if cached is not None:
        return cached
    vector = (await embed_texts([query]))[0]
    if key not in _QUERY_VECTOR_CACHE and len(_QUERY_VECTOR_CACHE) >= _QUERY_VECTOR_CACHE_MAX_ENTRIES:
        oldest = next(iter(_QUERY_VECTOR_CACHE))
        del _QUERY_VECTOR_CACHE[oldest]
    _QUERY_VECTOR_CACHE[key] = vector
    return vector


class EmbeddingToolIndex:
    """语义工具索引：rebuild 写工具向量，search 用查询向量按余弦相似度排序。

    工具向量在探测成功时就已算好并写入 pgvector 与进程缓存
    （sync_server_tool_embeddings / warm_vector_cache_from_db），
    因此 rebuild 只读缓存，不在这里发起阻塞式嵌入请求。
    查询向量由 prefetch_query_vector 异步预取后写入缓存，search 同样只读缓存。
    """

    def __init__(self) -> None:
        self._entries: tuple[Any, ...] = ()
        self._vectors: tuple[list[float], ...] = ()

    def rebuild(self, entries: tuple[Any, ...]) -> None:
        self._entries = entries
        if not entries:
            self._vectors = ()
            logger.info("工具语义索引清空 entries=0")
            return
        vectors: list[list[float]] = []
        for i, entry in enumerate(entries):
            name = getattr(entry, "name", None)
            if not isinstance(name, str) or name == "":
                raise ValueError(f"ToolEntry[{i}] name 无效")
            cached = _VECTOR_CACHE.get(name)
            if cached is None:
                raise LookupError(f"工具向量未同步，无法构建索引: {name}")
            vectors.append(cached)
        if len(vectors) != len(entries):
            raise RuntimeError(
                f"工具向量条数不符 got={len(vectors)} want={len(entries)}"
            )
        self._vectors = tuple(vectors)
        logger.info(
            "工具语义索引重建 entries=%s cache_size=%s",
            len(entries),
            len(_VECTOR_CACHE),
        )

    def search(self, query: str, *, limit: int = 5) -> list[ToolSearchResult]:
        if limit < 1:
            return []
        if not isinstance(query, str) or query.strip() == "":
            return []
        if not self._entries:
            return []
        if len(self._vectors) != len(self._entries):
            raise RuntimeError("工具语义索引未就绪：vectors 与 entries 长度不一致")

        query_key = content_hash(query.strip())
        query_vec = _QUERY_VECTOR_CACHE.get(query_key)
        if query_vec is None:
            # 查询向量必须由 prefetch_query_vector 异步预取后再到这里
            raise LookupError("查询向量未预取：无法执行语义检索")
        ranked: list[tuple[int, float]] = []
        for index, entry in enumerate(self._entries):
            score = _cosine(query_vec, self._vectors[index])
            # 名称精确命中加分，便于「直接点名工具」
            if entry.name.lower() == query.strip().lower():
                score += 1.0
            ranked.append((index, score))
        ranked.sort(key=lambda item: (-item[1], self._entries[item[0]].name))
        results = [
            ToolSearchResult(
                name=self._entries[index].name,
                description=self._entries[index].description,
                score=score,
            )
            for index, score in ranked[:limit]
        ]
        logger.info(
            "工具语义检索 query_len=%s top=%s best=%s",
            len(query),
            len(results),
            round(results[0].score, 4) if results else None,
        )
        return results


class PrefetchingEmbeddingToolset(SearchableToolset):
    """在同步检索前异步预取查询向量的可搜索工具集。

    adk_tool_search 的 index.search 是同步契约，但嵌入服务是网络调用。
    基类会在事件循环线程里直接做同步嵌入，把整个后端阻塞住。
    这里覆盖 search_tools：先异步算出查询向量写进缓存，
    再进入基类的同步检索（此时只读缓存，不再有网络调用）。
    """

    async def search_tools(
        self, query: str, tool_context: ToolContext
    ) -> list[dict[str, str | float]]:
        await prefetch_query_vector(query.strip())
        return await super().search_tools(query, tool_context)
