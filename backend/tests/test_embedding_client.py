"""嵌入客户端：真实服务连通 + 真实失败分支（错误 URL），不使用 mock。"""

from __future__ import annotations

import pytest

from app.config import get_settings
from app.services.embedding_client import EmbeddingServiceUnavailable, embed_texts


async def test_embed_texts_against_real_service(env: dict[str, str]) -> None:
    """真实嵌入服务连通：返回条数与维度必须与配置一致（消耗极少量额度）。"""

    settings = get_settings()
    vectors = await embed_texts(["向量客户端连通性验证"])
    assert len(vectors) == 1
    assert len(vectors[0]) == settings.embedding_dim


async def test_embed_texts_unreachable_url(monkeypatch) -> None:
    """服务地址不可达时归型为 EmbeddingServiceUnavailable（连接层真实失败）。"""

    monkeypatch.setenv("EMBEDDING_BASE_URL", "http://127.0.0.1:9")
    get_settings.cache_clear()
    try:
        with pytest.raises(EmbeddingServiceUnavailable, match="嵌入服务不可达"):
            await embed_texts(["不该被嵌入的文本"])
    finally:
        get_settings.cache_clear()


async def test_embed_texts_without_api_key_rejected_by_service(monkeypatch) -> None:
    """不带鉴权头调云端服务：真实 401 归型为 EmbeddingServiceUnavailable。

    反向证明鉴权头逻辑：EMBEDDING_API_KEY 清空后确实不带 Authorization。
    """

    monkeypatch.setenv("EMBEDDING_API_KEY", "")
    get_settings.cache_clear()
    try:
        with pytest.raises(EmbeddingServiceUnavailable, match="401"):
            await embed_texts(["未带密钥的请求"])
    finally:
        get_settings.cache_clear()
