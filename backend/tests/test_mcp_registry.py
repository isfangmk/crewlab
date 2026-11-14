"""MCP Registry：条目映射单测 + 真实搜索（不可达 skip）。"""

from __future__ import annotations

import httpx
import pytest

from app.services import mcp_registry_service
from app.services.mcp_registry_service import registry_entry_to_server_input
from tests.conftest import login_access_token


def test_remote_entry_maps_to_server_input() -> None:
    """remotes 条目映射为登记预填：嵌套 transport.type=sse → sse。"""

    entry = {
        "name": "io.example__filesystem",
        "description": "文件系统",
        "remotes": [
            {
                "transport": {"type": "sse"},
                "url": "https://example.com/mcp/sse",
            }
        ],
    }
    out = registry_entry_to_server_input(entry)
    assert out["name"] == "io.example-filesystem"
    assert out["transport"] == "sse"
    assert out["endpoint"] == "https://example.com/mcp/sse"
    assert out["hint"] == ""


def test_flat_transport_defaults_to_http() -> None:
    """transport 扁平字段（streamable-http 等）归一为 http。"""

    entry = {
        "name": "demo",
        "remotes": [{"type": "streamable-http", "url": "https://x.example/mcp"}],
    }
    out = registry_entry_to_server_input(entry)
    assert out["transport"] == "http"
    assert out["endpoint"] == "https://x.example/mcp"


def test_package_entry_gives_deploy_hint() -> None:
    """无 remotes 只有 packages：endpoint 留空，提示需自行部署。"""

    entry = {
        "name": "pkg-demo",
        "packages": [
            {"identifier": "@scope/server", "registryType": "npm"},
            {"identifier": "pypi-server", "registryType": "pypi"},
        ],
    }
    out = registry_entry_to_server_input(entry)
    assert out["endpoint"] == ""
    assert out["transport"] == "http"
    assert "@scope/server" in out["hint"]
    assert "需自行部署" in out["hint"]


def test_remote_without_url_falls_back_to_packages() -> None:
    """remote 缺 url 时不能误填 endpoint，回退包提示。"""

    entry = {
        "name": "mixed",
        "remotes": [{"transport": {"type": "sse"}, "url": ""}],
        "packages": [{"identifier": "npm-x", "registryType": "npm"}],
    }
    out = registry_entry_to_server_input(entry)
    assert out["endpoint"] == ""
    assert "npm-x" in out["hint"]


async def test_search_registry_real_call() -> None:
    """真实打官方注册表；网络不可达显式 skip，不做 mock。"""

    try:
        result = await mcp_registry_service.search_registry(query="filesystem", limit=5)
    except (httpx.HTTPError, TypeError) as exc:
        pytest.skip(f"Registry 不可达: {exc}")
    assert isinstance(result["items"], list)
    for item in result["items"]:
        assert isinstance(item["name"], str)
        assert item["transport"] in ("http", "sse")
    assert isinstance(result["next_cursor"], str)


async def test_registry_proxy_endpoint(api_base: str, env: dict[str, str]) -> None:
    """API 代理端点：可达时 200 且结构一致；不可达时显式 502。"""

    async with httpx.AsyncClient(base_url=api_base, timeout=60.0) as client:
        token = await login_access_token(client, env)
        headers = {"Authorization": f"Bearer {token}"}
        resp = await client.get(
            "/api/v1/mcp/registry/servers",
            headers=headers,
            params={"search": "web", "limit": 3},
        )
    if resp.status_code == 502:
        pytest.skip(f"Registry 不可达: {resp.text}")
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert isinstance(body["items"], list)
    assert isinstance(body["next_cursor"], str)
