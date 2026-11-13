"""调用嵌入服务生成向量；维度必须与配置一致。

端点是 OpenAI 兼容的 /v1/embeddings；是否带鉴权由 EMBEDDING_API_KEY 决定
（内网无鉴权服务留空，云端服务填密钥）。
"""

from __future__ import annotations

import logging

import httpx

from app.config import get_settings

logger = logging.getLogger(__name__)


class EmbeddingServiceUnavailable(RuntimeError):
    """嵌入服务不可用（网络失败 / HTTP 非 200）：环境或凭证问题，非调用方契约错误。

    与 RuntimeError 分型是为了 HTTP 映射：请求参数错误 400，服务不可用 503。
    """


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
    # encoding_format 必须显式给 float：部分兼容端点对数组输入缺省返回
    # base64 或直接判参数非法（SiliconFlow 20015）
    payload = {
        "model": settings.embedding_model,
        "input": texts,
        "encoding_format": "float",
    }
    headers = {}
    if settings.embedding_api_key != "":
        headers["Authorization"] = f"Bearer {settings.embedding_api_key}"
    logger.info(
        "调用嵌入服务 url=%s model=%s batch=%s",
        url,
        settings.embedding_model,
        len(texts),
    )
    try:
        async with httpx.AsyncClient(timeout=60.0) as client:
            resp = await client.post(url, json=payload, headers=headers)
    except httpx.RequestError as exc:
        raise EmbeddingServiceUnavailable(
            f"嵌入服务不可达 {url}: {type(exc).__name__}: {exc}"
        ) from exc
    if resp.status_code != 200:
        raise EmbeddingServiceUnavailable(
            f"嵌入服务 HTTP {resp.status_code}: {resp.text}"
        )

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
