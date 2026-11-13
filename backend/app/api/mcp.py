"""MCP Server CRUD / 探测 API。"""

from __future__ import annotations

import json

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import require_auth, require_not_viewer
from app.db import get_db
from app.services import mcp_service
from app.services.audit_service import write_audit
from app.services.auth_service import AuthUser

router = APIRouter(tags=["mcp"])

_DETAIL_LIMIT = 500


def _detail_summary(value: object) -> str:
    """审计只存参数/结果摘要，避免把整份 MCP 输出写进日志表。"""

    text = json.dumps(value, ensure_ascii=False, default=str)
    return text if len(text) <= _DETAIL_LIMIT else text[:_DETAIL_LIMIT] + "…"


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
    user: AuthUser = Depends(require_not_viewer),
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
    await write_audit(
        actor_user_id=user.id,
        action="mcp.server_create",
        resource_type="mcp_server",
        resource_id=str(row.id),
        detail={
            "name": row.name,
            "transport": row.transport,
            "endpoint": row.endpoint,
            "trusted": row.trusted,
            "enabled": row.enabled,
            "approval_policy": row.approval_policy,
        },
    )
    return mcp_service.server_to_wire(row)


@router.put("/mcp/servers/{server_id}")
async def update_server(
    server_id: int,
    body: McpServerInput,
    db: AsyncSession = Depends(get_db),
    user: AuthUser = Depends(require_not_viewer),
) -> dict:
    try:
        row = await mcp_service.update_server(
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
    await write_audit(
        actor_user_id=user.id,
        action="mcp.server_update",
        resource_type="mcp_server",
        resource_id=str(server_id),
        detail={
            "name": row.name,
            "transport": row.transport,
            "endpoint": row.endpoint,
            "trusted": row.trusted,
            "enabled": row.enabled,
            "approval_policy": row.approval_policy,
        },
    )
    return {"ok": True}


@router.delete("/mcp/servers/{server_id}")
async def delete_server(
    server_id: int,
    db: AsyncSession = Depends(get_db),
    user: AuthUser = Depends(require_not_viewer),
) -> None:
    try:
        # 删除前取名称：提交后行已消失，审计只能靠这里留痕。
        existing = await mcp_service.get_server(db, server_id)
        name = existing.name
        await mcp_service.delete_server(db, server_id)
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    await write_audit(
        actor_user_id=user.id,
        action="mcp.server_delete",
        resource_type="mcp_server",
        resource_id=str(server_id),
        detail={"name": name},
    )


@router.post("/mcp/servers/{server_id}/test")
async def test_server(
    server_id: int,
    db: AsyncSession = Depends(get_db),
    _user: str = Depends(require_not_viewer),
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
    user: AuthUser = Depends(require_not_viewer),
) -> dict:
    try:
        result = await mcp_service.run_tool(
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
    await write_audit(
        actor_user_id=user.id,
        action="mcp.tool_run",
        resource_type="mcp_server",
        resource_id=str(server_id),
        detail={
            "tool": tool_name,
            "arguments": _detail_summary(body.arguments),
            "result": _detail_summary(result),
        },
    )
    return result
