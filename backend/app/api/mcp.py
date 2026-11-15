"""MCP Server CRUD / 探测 API。"""

from __future__ import annotations

import json

import httpx
from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import HTMLResponse
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import require_auth, require_not_viewer
from app.db import get_db
from app.services.mcp import mcp_oauth_service
from app.services.mcp import mcp_registry_service
from app.services.mcp import mcp_service
from app.services.observability.audit_service import write_audit
from app.services.platform.auth_service import AuthUser

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
    enabled: bool
    # never | non_readonly | always；缺省 always
    approval_policy: str = "always"


@router.get("/mcp/servers")
async def list_servers(
    db: AsyncSession = Depends(get_db),
    _user: str = Depends(require_auth),
) -> dict:
    items = await mcp_service.list_servers(db)
    oauth_map = await mcp_oauth_service.status_map(db, [s.id for s in items])
    return {
        "items": [
            mcp_service.server_to_wire(s, oauth_status=oauth_map.get(s.id, "none"))
            for s in items
        ],
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
            "enabled": row.enabled,
            "approval_policy": row.approval_policy,
        },
    )
    return mcp_service.server_to_wire(row, oauth_status="none")


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
    user: AuthUser = Depends(require_not_viewer),
) -> dict:
    try:
        tools = await mcp_service.test_server(db, server_id)
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    # risk 只做展示，现算注入；不写 tools_cache_json（缓存保持纯工具描述）
    row = await mcp_service.get_server(db, server_id)
    risks = mcp_service.tool_risk_map(row)
    tools = [{**t, "risk": risks.get(t["name"], "approval_required")} for t in tools]
    await write_audit(
        actor_user_id=user.id,
        action="mcp.server_test",
        resource_type="mcp_server",
        resource_id=str(server_id),
        detail={"tool_count": len(tools)},
    )
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


@router.get("/mcp/servers/{server_id}/resources")
async def list_resources(
    server_id: int,
    db: AsyncSession = Depends(get_db),
    _user: AuthUser = Depends(require_auth),
) -> dict:
    try:
        items = await mcp_service.list_resources(db, server_id)
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    return {"items": items, "total": len(items)}


@router.get("/mcp/servers/{server_id}/resources/read")
async def read_resource(
    server_id: int,
    uri: str,
    db: AsyncSession = Depends(get_db),
    _user: AuthUser = Depends(require_auth),
) -> dict:
    try:
        return await mcp_service.read_resource(db, server_id, uri)
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc


@router.get("/mcp/servers/{server_id}/resource-templates")
async def list_resource_templates(
    server_id: int,
    db: AsyncSession = Depends(get_db),
    _user: AuthUser = Depends(require_auth),
) -> dict:
    try:
        items = await mcp_service.list_resource_templates(db, server_id)
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    return {"items": items, "total": len(items)}


@router.get("/mcp/servers/{server_id}/prompts")
async def list_prompts(
    server_id: int,
    db: AsyncSession = Depends(get_db),
    _user: AuthUser = Depends(require_auth),
) -> dict:
    try:
        items = await mcp_service.list_prompts(db, server_id)
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    return {"items": items, "total": len(items)}


class McpPromptGetInput(BaseModel):
    name: str = Field(min_length=1)
    arguments: dict[str, str] = Field(default_factory=dict)


@router.post("/mcp/servers/{server_id}/prompts/get")
async def get_prompt(
    server_id: int,
    body: McpPromptGetInput,
    db: AsyncSession = Depends(get_db),
    _user: AuthUser = Depends(require_auth),
) -> dict:
    # 纯读取远端 prompt 模板，不进审计：没有状态变化
    try:
        return await mcp_service.get_prompt(
            db, server_id, body.name, body.arguments
        )
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc


@router.get("/mcp/registry/servers")
async def search_registry(
    search: str = "",
    limit: int = 20,
    cursor: str = "",
    _user: AuthUser = Depends(require_auth),
) -> dict:
    """代理官方 MCP Registry 搜索；网络不可达显式报 502，前端给重试。"""

    try:
        return await mcp_registry_service.search_registry(
            query=search, limit=limit, cursor=cursor
        )
    except (httpx.HTTPError, TypeError) as exc:
        raise HTTPException(status_code=502, detail=f"Registry 不可达: {exc}") from exc


# ---------- OAuth 授权 ----------------------------------------------------------

_OAUTH_DONE_HTML = """<!doctype html>
<html lang="zh-CN"><head><meta charset="utf-8"><title>授权完成</title></head>
<body style="font-family:sans-serif;padding:2rem">
<h2>授权完成</h2>
<p>可以关闭本窗口返回 CrewLab 继续操作。</p>
<script>window.close();</script>
<a href="/mcp">返回 MCP 服务页</a>
</body></html>"""


def _oauth_error_html(message: str) -> str:
    """授权失败页：只给错误说明，不回显任何密钥/授权码。"""

    return (
        "<!doctype html><html lang=\"zh-CN\"><head><meta charset=\"utf-8\">"
        "<title>授权失败</title></head><body style=\"font-family:sans-serif;padding:2rem\">"
        f"<h2>授权失败</h2><p>{message}</p><a href=\"/mcp\">返回 MCP 服务页</a>"
        "</body></html>"
    )


class McpOauthTokenInput(BaseModel):
    access_token: str = Field(min_length=1)
    refresh_token: str = ""
    expires_in: float | None = Field(default=None, gt=0)


@router.post("/mcp/servers/{server_id}/oauth/start")
async def oauth_start(
    server_id: int,
    db: AsyncSession = Depends(get_db),
    user: AuthUser = Depends(require_not_viewer),
) -> dict:
    """发起 OAuth 授权：发现 + 动态注册 + PKCE，返回授权页 URL。"""

    try:
        server = await mcp_service.get_server(db, server_id)
        result = await mcp_oauth_service.start_auth(db, server)
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except (httpx.HTTPError, OSError) as exc:
        raise HTTPException(status_code=502, detail=f"授权服务器不可达: {exc}") from exc
    await write_audit(
        actor_user_id=user.id,
        action="mcp.oauth_start",
        resource_type="mcp_server",
        resource_id=str(server_id),
        detail={"name": server.name},
    )
    return result


@router.get("/mcp/oauth/callback", response_class=HTMLResponse)
async def oauth_callback(
    code: str = "",
    state: str = "",
    db: AsyncSession = Depends(get_db),
) -> HTMLResponse:
    """授权回调（免登录）：换 token 后返回结果页，不回显任何密钥。"""

    if code == "" or state == "":
        return HTMLResponse(_oauth_error_html("缺少 code 或 state 参数"), status_code=400)
    try:
        await mcp_oauth_service.handle_callback(db, code=code, state=state)
    except ValueError as exc:
        return HTMLResponse(_oauth_error_html(str(exc)), status_code=400)
    except (httpx.HTTPError, OSError) as exc:
        return HTMLResponse(
            _oauth_error_html(f"授权服务器不可达: {exc}"), status_code=502
        )
    return HTMLResponse(_OAUTH_DONE_HTML)


@router.post("/mcp/servers/{server_id}/oauth/token")
async def oauth_save_token(
    server_id: int,
    body: McpOauthTokenInput,
    db: AsyncSession = Depends(get_db),
    user: AuthUser = Depends(require_not_viewer),
) -> dict:
    """手动粘贴 Token 兜底（不支持动态注册的服务用）。"""

    try:
        await mcp_service.get_server(db, server_id)
        status = await mcp_oauth_service.save_manual_token(
            db,
            server_id,
            access_token=body.access_token,
            refresh_token=body.refresh_token,
            expires_in=body.expires_in,
        )
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    await write_audit(
        actor_user_id=user.id,
        action="mcp.oauth_token_save",
        resource_type="mcp_server",
        resource_id=str(server_id),
        detail={"auth_source": "manual"},
    )
    return status


@router.get("/mcp/servers/{server_id}/oauth/status")
async def oauth_status(
    server_id: int,
    db: AsyncSession = Depends(get_db),
    _user: AuthUser = Depends(require_auth),
) -> dict:
    try:
        await mcp_service.get_server(db, server_id)
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return await mcp_oauth_service.oauth_status(db, server_id)


@router.delete("/mcp/servers/{server_id}/oauth")
async def oauth_revoke(
    server_id: int,
    db: AsyncSession = Depends(get_db),
    user: AuthUser = Depends(require_not_viewer),
) -> dict:
    try:
        existing = await mcp_service.get_server(db, server_id)
        name = existing.name
        await mcp_oauth_service.revoke(db, server_id)
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    await write_audit(
        actor_user_id=user.id,
        action="mcp.oauth_revoke",
        resource_type="mcp_server",
        resource_id=str(server_id),
        detail={"name": name},
    )
    return {"ok": True}
