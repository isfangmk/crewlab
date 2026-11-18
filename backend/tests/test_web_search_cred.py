"""联网搜索凭证：从环境变量读取，与设置页 LLM provider 解耦。

用 monkeypatch.setenv 改配置后清缓存（与 test_embedding_client.py 同范式），
验证凭证来源、缺省失败分支；不 mock HTTP、不伪造模型输出。
"""

from __future__ import annotations

import asyncio

import pytest

from app.config import get_settings
from app.services.platform.web_search_service import resolve_deepseek_search_cred


def _resolve() -> tuple[str, str]:
    """db 传 None：函数体不再读库，证明与 provider 解耦。"""
    return asyncio.run(resolve_deepseek_search_cred(None))  # pyright: ignore[reportArgumentType]


def test_resolve_cred_from_env(monkeypatch: pytest.MonkeyPatch) -> None:
    """凭证来自 WEB_SEARCH_API_KEY / WEB_SEARCH_BASE_URL，不依赖 DB 里的 provider。"""
    monkeypatch.setenv("WEB_SEARCH_API_KEY", "test-web-search-key")
    monkeypatch.setenv("WEB_SEARCH_BASE_URL", "https://search.example.com")
    get_settings.cache_clear()
    try:
        base_url, api_key = _resolve()
        assert api_key == "test-web-search-key"
        assert base_url == "https://search.example.com"
    finally:
        get_settings.cache_clear()


def test_resolve_cred_defaults_base_url(monkeypatch: pytest.MonkeyPatch) -> None:
    """base_url 未配置时回退 DeepSeek 官方地址。"""
    monkeypatch.setenv("WEB_SEARCH_API_KEY", "k")
    monkeypatch.delenv("WEB_SEARCH_BASE_URL", raising=False)
    get_settings.cache_clear()
    try:
        base_url, api_key = _resolve()
        assert api_key == "k"
        assert base_url == "https://api.deepseek.com"
    finally:
        get_settings.cache_clear()


def test_resolve_cred_missing_key_fails_explicitly(monkeypatch: pytest.MonkeyPatch) -> None:
    """缺 WEB_SEARCH_API_KEY 时显式失败，让调用方跳过挂载/退回内网取证。"""
    monkeypatch.setenv("WEB_SEARCH_API_KEY", "")
    get_settings.cache_clear()
    try:
        with pytest.raises(LookupError, match="WEB_SEARCH_API_KEY 未配置"):
            _resolve()
    finally:
        get_settings.cache_clear()
