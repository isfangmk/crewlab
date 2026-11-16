"""MCP Server 探活：轻量健康检查只维护状态三字段（禁 mock）。

失败路径用不可达端点真实连接失败；成功路径起真实 SSE 子进程，
起不来则 skip；单轮循环只探启用的 Server。
"""

from __future__ import annotations

import socket
import subprocess
import sys
import time
import uuid
from pathlib import Path

from app.db import SessionLocal
from app.models.mcp import McpServer
from app.services.mcp.mcp_health import probe_enabled_servers_once
from app.services.mcp.mcp_service import probe_server_health

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


async def _add_server(name: str, endpoint: str, *, enabled: bool = True) -> int:
    async with SessionLocal() as db:
        row = McpServer(name=name, transport="sse", endpoint=endpoint, enabled=enabled)
        db.add(row)
        await db.commit()
        await db.refresh(row)
        return row.id


async def _drop_server(server_id: int) -> None:
    async with SessionLocal() as db:
        row = await db.get(McpServer, server_id)
        if row is not None:
            await db.delete(row)
            await db.commit()


async def test_probe_unreachable_marks_error() -> None:
    """不可达端点探活返回失败：status=error、记录错误与探测时间。"""

    name = f"health-probe-{uuid.uuid4().hex[:8]}"
    server_id = await _add_server(name, "http://127.0.0.1:9/mcp")
    try:
        async with SessionLocal() as db:
            row = await db.get(McpServer, server_id)
            ok = await probe_server_health(db, row)
            assert ok is False
            assert row.status == "error"
            assert row.last_error != ""
            assert row.last_probed_at is not None
    finally:
        await _drop_server(server_id)


async def test_probe_live_server_marks_ok() -> None:
    """真实 SSE 服务探活成功：status=ok、错误清空、探测时间已写。"""

    spawned = _spawn_server()
    if spawned is None:
        return
    proc, endpoint = spawned
    name = f"health-probe-{uuid.uuid4().hex[:8]}"
    server_id = await _add_server(name, endpoint)
    try:
        async with SessionLocal() as db:
            row = await db.get(McpServer, server_id)
            ok = await probe_server_health(db, row)
            assert ok is True
            assert row.status == "ok"
            assert row.last_error == ""
            assert row.last_probed_at is not None
    finally:
        await _drop_server(server_id)
        proc.terminate()


async def test_round_probes_only_enabled() -> None:
    """单轮只探启用的 Server：停用行状态不动。"""

    tag = uuid.uuid4().hex[:8]
    on_id = await _add_server(f"health-on-{tag}", "http://127.0.0.1:9/mcp", enabled=True)
    off_id = await _add_server(f"health-off-{tag}", "http://127.0.0.1:9/mcp", enabled=False)
    try:
        async with SessionLocal() as db:
            await probe_enabled_servers_once(db)
        async with SessionLocal() as db:
            on_row = await db.get(McpServer, on_id)
            assert on_row.status == "error"
            assert on_row.last_probed_at is not None
            off_row = await db.get(McpServer, off_id)
            assert off_row.status == ""
            assert off_row.last_probed_at is None
    finally:
        await _drop_server(on_id)
        await _drop_server(off_id)
