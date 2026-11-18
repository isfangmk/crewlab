"""MCP stdio 传输：连接参数构造 + 真实 codebase-memory-mcp 全链路探测。

测试全部使用真实依赖（真 ADK 参数类、真 SQLite、真二进制），不 mock。
测试产生的临时 server 行在 finally 中删除，不污染用户数据。
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest
from sqlalchemy import select

from app.db import SessionLocal
from app.models.mcp import McpServer
from app.services.mcp.mcp_service import (
    connection_params_for_server,
    create_server,
    delete_server,
)
from app.services.mcp.mcp_service import (
    test_server as probe_mcp_server,
)

# 进程内装的真实二进制；缺失时相关用例 skip（CI 未必装过）
CBM_EXE = Path(
    os.environ.get(
        "CBM_BIN",
        r"C:/Users/admin/AppData/Roaming/npm/node_modules/codebase-memory-mcp/bin/codebase-memory-mcp.exe",
    )
)
TEST_SERVER_NAME = "codebase-memory-stdio-test"


def _stdio_row(**overrides: object) -> McpServer:
    values: dict[str, object] = dict(
        id=999999,
        name="stdio-unit",
        transport="stdio",
        endpoint="",
        command="C:/tools/mcp.exe",
        args_json='["--ui=false"]',
        cwd="C:/repo",
        env_json='{"CBM_CACHE_DIR":"D:/cbm-cache"}',
        credential="",
        header_template_json="{}",
        approval_policy="non_readonly",
        enabled=True,
        status="ok",
        last_error="",
        tools_cache_json="[]",
    )
    values.update(overrides)
    return McpServer(**values)  # type: ignore[arg-type]


@pytest.mark.asyncio
async def test_stdio_connection_params_shape() -> None:
    """stdio 分支应产出 StdioConnectionParams，command/args/cwd/env 正确。"""

    from google.adk.tools.mcp_tool.mcp_session_manager import StdioConnectionParams

    async with SessionLocal() as db:
        params = await connection_params_for_server(db, _stdio_row())
    assert isinstance(params, StdioConnectionParams)
    server_params = params.server_params
    assert server_params.command == "C:/tools/mcp.exe"
    assert server_params.args == ["--ui=false"]
    assert server_params.cwd == "C:/repo"
    assert server_params.env == {"CBM_CACHE_DIR": "D:/cbm-cache"}
    assert params.timeout == 30


@pytest.mark.asyncio
async def test_stdio_env_placeholder_requires_credential() -> None:
    """env 里的 {{字段}} 在无凭证时必须显式失败，不能静默下发空值。"""

    row = _stdio_row(env_json='{"TOKEN":"{{token}}"}', credential="")
    async with SessionLocal() as db:
        with pytest.raises(ValueError, match="占位符|凭证"):
            await connection_params_for_server(db, row)


@pytest.mark.asyncio
async def test_stdio_rejects_bad_json() -> None:
    """args/env 非法 JSON 形状应显式报错。"""

    from app.services.mcp.mcp_service import validate_server_config

    base = dict(
        name="bad", transport="stdio", endpoint="", command="x",
        header_template_json="{}", approval_policy="always",
        env_json="[]",  # env 必须是映射
        args_json="[]",
    )
    with pytest.raises(TypeError, match="env_json"):
        validate_server_config(**base)  # type: ignore[arg-type]


@pytest.mark.asyncio
async def test_stdio_real_binary_probe_and_tools() -> None:
    """用真实二进制跑一次探测：应拿到 17 个工具并标记 status=ok。"""

    if not CBM_EXE.is_file():
        pytest.skip(f"未找到 codebase-memory-mcp 二进制: {CBM_EXE}")

    created = False
    async with SessionLocal() as db:
        try:
            # 先清掉可能的上次遗留（同名唯一约束）
            stale = await db.scalar(
                select(McpServer).where(McpServer.name == TEST_SERVER_NAME)
            )
            if stale is not None:
                await delete_server(db, stale.id)

            row = await create_server(
                db,
                name=TEST_SERVER_NAME,
                transport="stdio",
                endpoint="",
                command=str(CBM_EXE),
                args_json="[]",
                cwd="",
                env_json="{}",
                credential="",
                header_template_json="{}",
                approval_policy="never",
                enabled=True,
            )
            created = True
            assert row.status == "ok", row.last_error
            items = await probe_mcp_server(db, row.id)
            names = {item["name"] for item in items}
            assert len(items) >= 17, f"工具数不足: {sorted(names)}"
            for expected in ("index_repository", "search_graph", "get_architecture", "trace_path"):
                assert expected in names, f"缺少工具 {expected}"
        finally:
            if created:
                leftover = await db.scalar(
                    select(McpServer).where(McpServer.name == TEST_SERVER_NAME)
                )
                if leftover is not None:
                    await delete_server(db, leftover.id)
