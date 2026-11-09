"""MCP Server CRUD / 探测 API。"""

from __future__ import annotations

import json

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import require_auth
from app.db import get_db
from app.services import mcp_service

router = APIRouter(tags=["mcp"])


class McpServerInput(BaseModel):
    name: str = Field(min_length=1)
    transport: str = Field(min_length=1)
    endpoint: str = Field(min_length=1)
    credential: str
    header_template_json: str
    trusted: bool
    enabled: bool
    # never | non_readonly | always；缺省 always
    approval_policy: str = "always"


@router.get("/mcp/servers")
async def list_servers(
    db: AsyncSession = Depends(get_db),
    _user: str = Depends(require_auth),
) -> dict:
    items = await mcp_service.list_servers(db)
    return {
        "items": [mcp_service.server_to_wire(s) for s in items],
        "total": len(items),
    }


@router.post("/mcp/servers")
async def create_server(
    body: McpServerInput,
    db: AsyncSession = Depends(get_db),
    _user: str = Depends(require_auth),
) -> dict:
    try:
        row = await mcp_service.create_server(
            db,
            name=body.name,
            transport=body.transport,
            endpoint=body.endpoint,
            credential=body.credential,
            header_template_json=body.header_template_json,
            trusted=body.trusted,
            enabled=body.enabled,
            approval_policy=body.approval_policy,
        )
    except (ValueError, TypeError, json.JSONDecodeError) as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except (ConnectionError, OSError) as exc:
        # MCP 端点不可达：新建流程已回滚删除该 Server，此处显式返回 502，
        # 不能让连接异常冒泡成 500 的 ASGI traceback。
        raise HTTPException(status_code=502, detail=f"MCP 连接失败: {exc}") from exc
    return mcp_service.server_to_wire(row)


@router.put("/mcp/servers/{server_id}")
async def update_server(
    server_id: int,
    body: McpServerInput,
    db: AsyncSession = Depends(get_db),
    _user: str = Depends(require_auth),
) -> dict:
    try:
        await mcp_service.update_server(
            db,
            server_id,
            name=body.name,
            transport=body.transport,
            endpoint=body.endpoint,
            credential=body.credential,
            header_template_json=body.header_template_json,
            trusted=body.trusted,
            enabled=body.enabled,
            approval_policy=body.approval_policy,
        )
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except (ValueError, TypeError, json.JSONDecodeError) as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except (ConnectionError, OSError) as exc:
        # MCP 端点不可达：显式返回 502，不冒泡成 500
        raise HTTPException(status_code=502, detail=f"MCP 连接失败: {exc}") from exc
    return {"ok": True}


@router.delete("/mcp/servers/{server_id}")
async def delete_server(
    server_id: int,
    db: AsyncSession = Depends(get_db),
    _user: str = Depends(require_auth),
) -> None:
    try:
        await mcp_service.delete_server(db, server_id)
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.post("/mcp/servers/{server_id}/test")
async def test_server(
    server_id: int,
    db: AsyncSession = Depends(get_db),
    _user: str = Depends(require_auth),
) -> dict:
    try:
        tools = await mcp_service.test_server(db, server_id)
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    return {"tools": tools, "count": len(tools)}


class McpToolRunInput(BaseModel):
    arguments: dict


@router.post("/mcp/servers/{server_id}/tools/{tool_name}/run")
async def run_tool(
    server_id: int,
    tool_name: str,
    body: McpToolRunInput,
    db: AsyncSession = Depends(get_db),
    _user: str = Depends(require_auth),
) -> dict:
    try:
        return await mcp_service.run_tool(
            db,
            server_id,
            tool_name=tool_name,
            arguments=body.arguments,
        )
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except (ValueError, TypeError) as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
