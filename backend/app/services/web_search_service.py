"""DeepSeek 官方网页搜索：POST /v1/tools/search。"""

from __future__ import annotations

import logging

import httpx
from sqlalchemy.ext.asyncio import AsyncSession

from app.services.llm_config import get_provider

logger = logging.getLogger(__name__)


def _search_url(base_url: str) -> str:
    if base_url == "":
        raise ValueError("deepseek base_url 不能为空")
    root = base_url.rstrip("/")
    if root.endswith("/v1"):
        return f"{root}/tools/search"
    return f"{root}/v1/tools/search"


async def resolve_deepseek_search_cred(db: AsyncSession) -> tuple[str, str]:
    """联网搜索固定走配置中的 deepseek provider（与官方 Search API 同源密钥）。

    provider id 写死为 deepseek 是有意的：DeepSeek Search API 与
    DeepSeek 聊天接口同源，换 provider 就换不了搜索能力。
    改名/删除 deepseek provider 时这里会显式失败，不静默降级。
    """

    provider = await get_provider(db, "deepseek")
    if provider.api_key == "":
        raise LookupError("deepseek provider 的 api_key 为空，无法调用网页搜索")
    return provider.base_url, provider.api_key


async def deepseek_web_search(
    *,
    base_url: str,
    api_key: str,
    query: str,
    count: int = 5,
) -> dict:
    """调用 DeepSeek Search API，返回精简结果供模型引用。"""

    if not isinstance(query, str) or query == "":
        raise ValueError("query 不能为空")
    if len(query) > 128:
        raise ValueError("query 最长 128 字符")
    if count < 1 or count > 50:
        raise ValueError("count 范围是 1..50")
    if api_key == "":
        raise ValueError("api_key 不能为空")

    url = _search_url(base_url)
    payload = {"query": query, "count": count, "need_content": False}
    logger.info("DeepSeek 网页搜索 url=%s query_len=%s count=%s", url, len(query), count)
    async with httpx.AsyncClient(timeout=30.0) as client:
        resp = await client.post(
            url,
            headers={"Authorization": f"Bearer {api_key}"},
            json=payload,
        )
    if resp.status_code != 200:
        raise RuntimeError(f"DeepSeek 搜索 HTTP {resp.status_code}: {resp.text[:500]}")

    body = resp.json()
    if "results" not in body:
        raise RuntimeError("DeepSeek 搜索响应缺少 results")
    rows = body["results"]
    if not isinstance(rows, list):
        raise TypeError("results 必须是数组")

    items: list[dict] = []
    for i, row in enumerate(rows):
        if not isinstance(row, dict):
            raise TypeError(f"results[{i}] 必须是对象")
        for key in ("title", "url", "snippet", "summary"):
            if key not in row:
                raise RuntimeError(f"results[{i}] 缺少 {key}")
        items.append(
            {
                "title": row["title"],
                "url": row["url"],
                "snippet": row["snippet"],
                "summary": row["summary"],
                "date": row.get("date"),
                "rerank_score": row.get("rerank_score"),
            }
        )
    logger.info("DeepSeek 网页搜索完成 hits=%s", len(items))
    return {"provider": "deepseek", "query": query, "results": items, "total": len(items)}
