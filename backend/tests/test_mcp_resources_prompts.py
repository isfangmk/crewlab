"""MCP 资源 / 提示词：探测缓存与实时读取端到端。

起本地真实 MCP Server（SSE 子进程，带资源与提示词），经真实 HTTP API
登记探测，断言三个 cache 列与 last_probed_at 落库、读取/取提示词返回
契约结构。子进程起不来则 skip（禁 mock）。
"""

from __future__ import annotations

import json
import socket
import subprocess
import sys
import time
import uuid
from pathlib import Path

import httpx
import pytest

from tests.conftest import login_access_token

_SCRIPT = Path(__file__).resolve().parent / "mcp_extras_server.py"


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return int(s.getsockname()[1])


def _spawn_server() -> tuple[subprocess.Popen, str] | None:
    """拉起测试 MCP Server 子进程；15 秒内端口不通返回 None（调用方 skip）。"""

    port = _free_port()
    proc = subprocess.Popen(
        [sys.executable, str(_SCRIPT), str(port)],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    deadline = time.monotonic() + 15.0
    while time.monotonic() < deadline:
        if proc.poll() is not None:
            return None
        try:
            with socket.create_connection(("127.0.0.1", port), timeout=0.5):
                return proc, f"http://127.0.0.1:{port}/sse"
        except OSError:
            time.sleep(0.3)
    proc.terminate()
    return None


async def _login(client: httpx.AsyncClient, env: dict[str, str]) -> dict[str, str]:
    token = await login_access_token(client, env)
    return {"Authorization": f"Bearer {token}"}


async def test_resources_prompts_probe_and_read(
    api_base: str, env: dict[str, str]
) -> None:
    spawned = _spawn_server()
    if spawned is None:
        pytest.skip("本地 MCP Server 子进程起不来")
    proc, endpoint = spawned
    name = f"mcp-extras-{uuid.uuid4().hex[:8]}"
    server_id = 0
    try:
        async with httpx.AsyncClient(base_url=api_base, timeout=120.0) as client:
            headers = await _login(client, env)
            # 登记即探测：create 流程会同步写 tools 与资源/提示词缓存
            resp = await client.post(
                "/api/v1/mcp/servers",
                headers=headers,
                json={
                    "name": name,
                    "transport": "sse",
                    "endpoint": endpoint,
                    "credential": "",
                    "header_template_json": "{}",
                    "enabled": True,
                    "approval_policy": "always",
                },
            )
            assert resp.status_code == 200, resp.text
            body = resp.json()
            server_id = body["id"]
            # 列表 wire 契约：资源/提示词计数与探测时间
            assert body["resources_count"] == 1, body
            assert body["prompts_count"] == 1, body
            assert body["last_probed_at"], body

            # 缓存列落库结构（ORM 直查，断言探测写入的原始契约）
            from sqlalchemy import select

            from app.db import SessionLocal
            from app.models.mcp import McpServer

            async with SessionLocal() as db:
                row = await db.scalar(select(McpServer).where(McpServer.name == name))
                assert row is not None
                assert row.status == "ok"
                assert row.last_probed_at is not None
                resources = json.loads(row.resources_cache_json)
                assert [r["uri"] for r in resources] == ["demo://greeting"]
                assert resources[0]["name"] == "greeting"
                assert resources[0]["mime_type"] == "text/plain"
                templates = json.loads(row.resource_templates_cache_json)
                assert [t["uri_template"] for t in templates] == [
                    "demo://notes/{note_id}"
                ]
                prompts = json.loads(row.prompts_cache_json)
                assert [p["name"] for p in prompts] == ["summarize"]
                arg_names = [a["name"] for a in prompts[0]["arguments"]]
                assert arg_names == ["topic"]
                assert prompts[0]["arguments"][0]["required"] is True

            # 实时资源清单
            resp = await client.get(
                f"/api/v1/mcp/servers/{server_id}/resources", headers=headers
            )
            assert resp.status_code == 200, resp.text
            items = resp.json()["items"]
            assert items[0]["uri"] == "demo://greeting"
            assert items[0]["name"] == "greeting"

            # 读取资源内容
            resp = await client.get(
                f"/api/v1/mcp/servers/{server_id}/resources/read",
                headers=headers,
                params={"uri": "demo://greeting"},
            )
            assert resp.status_code == 200, resp.text
            data = resp.json()
            assert data["uri"] == "demo://greeting"
            assert data["contents"][0]["text"] == "你好，这是测试资源内容"
            assert data["contents"][0]["mime_type"] == "text/plain"

            # 资源模板
            resp = await client.get(
                f"/api/v1/mcp/servers/{server_id}/resource-templates",
                headers=headers,
            )
            assert resp.status_code == 200, resp.text
            templates = resp.json()["items"]
            assert templates[0]["uri_template"] == "demo://notes/{note_id}"

            # 提示词清单与按参取消息
            resp = await client.get(
                f"/api/v1/mcp/servers/{server_id}/prompts", headers=headers
            )
            assert resp.status_code == 200, resp.text
            prompts = resp.json()["items"]
            assert prompts[0]["name"] == "summarize"

            resp = await client.post(
                f"/api/v1/mcp/servers/{server_id}/prompts/get",
                headers=headers,
                json={"name": "summarize", "arguments": {"topic": "测试主题"}},
            )
            assert resp.status_code == 200, resp.text
            got = resp.json()
            assert got["name"] == "summarize"
            assert got["messages"], got
            assert got["messages"][0]["role"] in ("user", "assistant")
            assert "测试主题" in got["messages"][0]["text"]

            # 契约显式失败：uri 为空 400，prompt 名为空 400
            resp = await client.get(
                f"/api/v1/mcp/servers/{server_id}/resources/read",
                headers=headers,
                params={"uri": ""},
            )
            assert resp.status_code == 400, resp.text
            resp = await client.post(
                f"/api/v1/mcp/servers/{server_id}/prompts/get",
                headers=headers,
                json={"name": "  ", "arguments": {}},
            )
            assert resp.status_code == 400, resp.text
    finally:
        if server_id:
            async with httpx.AsyncClient(base_url=api_base, timeout=60.0) as client:
                try:
                    headers = await _login(client, env)
                    await client.delete(
                        f"/api/v1/mcp/servers/{server_id}", headers=headers
                    )
                except Exception:  # noqa: BLE001 - 清理失败不覆盖原始断言错误
                    pass
        proc.terminate()
        try:
            proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            proc.kill()


async def test_catalog_toolset_injects_resource_loader() -> None:
    """有资源缓存时目录 toolset 注入 load_mcp_resource；无资源时不注入。"""

    from google.adk.tools.mcp_tool.mcp_session_manager import SseConnectionParams

    from app.models.mcp import McpServer
    from app.services.mcp_catalog_toolset import CachedMcpCatalogToolset

    tools_json = json.dumps(
        [{"name": "echo", "description": "回显", "input_schema": {"type": "object"}}],
        ensure_ascii=False,
    )
    resources_json = json.dumps(
        [
            {
                "uri": "demo://greeting",
                "name": "greeting",
                "description": "",
                "mime_type": "text/plain",
            }
        ],
        ensure_ascii=False,
    )
    params = SseConnectionParams(url="http://127.0.0.1:1/sse")
    row = McpServer(
        id=1,
        name="extras-demo",
        transport="sse",
        endpoint="http://127.0.0.1:1/sse",
        credential="",
        header_template_json="{}",
        approval_policy="never",
        enabled=True,
        status="ok",
        last_error="",
        tools_cache_json=tools_json,
        resources_cache_json=resources_json,
        prompts_cache_json="[]",
        resource_templates_cache_json="[]",
    )
    toolset = CachedMcpCatalogToolset(row, params)
    tools = await toolset.get_tools()
    names = [t.name for t in tools]
    assert "mcp__extras-demo__echo" in names
    assert "mcp__extras-demo__load_mcp_resource" in names
    assert await toolset.list_resources() == ["greeting"]
    await toolset.close()

    row.resources_cache_json = "[]"
    toolset2 = CachedMcpCatalogToolset(row, params)
    tools2 = await toolset2.get_tools()
    names2 = [t.name for t in tools2]
    assert "mcp__extras-demo__load_mcp_resource" not in names2
    assert await toolset2.list_resources() == []
    await toolset2.close()
