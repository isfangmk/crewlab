"""调用 br-embedding 生成向量；维度必须与配置一致。"""

from __future__ import annotations

import logging

import httpx

from app.config import get_settings

logger = logging.getLogger(__name__)


async def embed_texts(texts: list[str]) -> list[list[float]]:
    """批量嵌入；任一条维度不符或响应缺字段则显式失败。"""

    if not texts:
        raise ValueError("texts 不能为空")
    for i, t in enumerate(texts):
        if not isinstance(t, str):
            raise TypeError(f"texts[{i}] 必须是字符串")
        if t == "":
            raise ValueError(f"texts[{i}] 不能为空")

    settings = get_settings()
    url = f"{settings.embedding_base_url.rstrip('/')}/v1/embeddings"
    payload = {"model": settings.embedding_model, "input": texts}
    logger.info(
        "调用嵌入服务 url=%s model=%s batch=%s",
        url,
        settings.embedding_model,
        len(texts),
    )
    async with httpx.AsyncClient(timeout=60.0) as client:
        resp = await client.post(url, json=payload)
    if resp.status_code != 200:
        raise RuntimeError(f"嵌入服务 HTTP {resp.status_code}: {resp.text}")

    body = resp.json()
    if "data" not in body:
        raise RuntimeError("嵌入响应缺少 data")
    rows = body["data"]
    if len(rows) != len(texts):
        raise RuntimeError(f"嵌入条数不符 got={len(rows)} want={len(texts)}")

    vectors: list[list[float]] = []
    for i, row in enumerate(rows):
        if "embedding" not in row:
            raise RuntimeError(f"嵌入 data[{i}] 缺少 embedding")
        vec = row["embedding"]
        if not isinstance(vec, list):
            raise TypeError(f"嵌入 data[{i}].embedding 必须是数组")
        if len(vec) != settings.embedding_dim:
            raise RuntimeError(
                f"嵌入维度不符 index={i} got={len(vec)} want={settings.embedding_dim}"
            )
        vectors.append(vec)
    return vectors


async def embed_text(text: str) -> list[float]:
    """单条嵌入。"""

    vectors = await embed_texts([text])
    return vectors[0]
