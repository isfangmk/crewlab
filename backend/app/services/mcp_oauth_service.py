"""MCP OAuth 授权：发现 → 动态注册 → 授权码 + PKCE → token 刷新。

按 MCP 规范实现精简客户端流程（RFC 9728 资源元数据 → RFC 8414 AS 元数据 →
RFC 7591 动态注册 → 授权码 + PKCE S256）。不走 mcp SDK 的 OAuthClientProvider：
它是 httpx2.Auth 的交互式单客户端模型，不适合服务器端多租户场景。
Token 服务级共享（admin 授权一次全工作台生效）、Fernet 加密落库；
另支持手动粘贴 Token 兜底（无 client 上下文，不自动刷新）。
"""

from __future__ import annotations

import base64
import hashlib
import logging
import secrets
from datetime import datetime, timedelta, timezone
from typing import Any
from urllib.parse import urlencode, urlsplit

import httpx
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.models_orm.mcp import McpServer
from app.models_orm.mcp_oauth import McpOauthState, McpOauthToken
from app.services.secrets_service import decrypt_json, encrypt_json

logger = logging.getLogger(__name__)

REQUEST_TIMEOUT = 15.0
STATE_TTL_MINUTES = 10
REFRESH_LEEWAY_SECONDS = 60
OAUTH_CALLBACK_PATH = "/api/v1/mcp/oauth/callback"


def redirect_uri() -> str:
    """OAuth 回调地址：需浏览器可达，缺省回退 http://{host}:{port}。"""

    settings = get_settings()
    base = (settings.public_base_url or f"http://{settings.host}:{settings.port}")
    return f"{base.rstrip('/')}{OAUTH_CALLBACK_PATH}"


async def discover_metadata(endpoint: str) -> dict[str, Any]:
    """发现授权服务器元数据：先 RFC 9728 资源元数据，再回退 RFC 8414 直查。"""

    parts = urlsplit(endpoint)
    if parts.scheme not in ("http", "https") or parts.netloc == "":
        raise ValueError(f"endpoint 不是合法 URL: {endpoint}")
    origin = f"{parts.scheme}://{parts.netloc}"
    async with httpx.AsyncClient(timeout=REQUEST_TIMEOUT) as client:
        # 第一步：受保护资源元数据（MCP 规范要求的发现入口）
        try:
            resp = await client.get(f"{origin}/.well-known/oauth-protected-resource")
            if resp.status_code == 200:
                data = resp.json()
                servers = (
                    data.get("authorization_servers") if isinstance(data, dict) else None
                )
                if isinstance(servers, list) and servers and isinstance(servers[0], str):
                    return await _fetch_as_metadata(client, servers[0])
        except (httpx.HTTPError, ValueError):
            pass
        # 第二步：直接按 RFC 8414 / OIDC 查 endpoint 所在域
        return await _fetch_as_metadata(client, origin)


async def _fetch_as_metadata(client: httpx.AsyncClient, issuer: str) -> dict[str, Any]:
    base = issuer.rstrip("/")
    for path in (
        "/.well-known/oauth-authorization-server",
        "/.well-known/openid-configuration",
    ):
        try:
            resp = await client.get(f"{base}{path}")
            if resp.status_code != 200:
                continue
            data = resp.json()
            if not isinstance(data, dict):
                continue
            if not data.get("authorization_endpoint") or not data.get("token_endpoint"):
                continue
            return data
        except (httpx.HTTPError, ValueError):
            continue
    raise ValueError(f"无法发现 OAuth 授权服务器元数据: {issuer}")


async def register_client(metadata: dict[str, Any]) -> dict[str, Any]:
    """RFC 7591 动态注册；失败显式报错并提示手动粘贴 Token 兜底。"""

    registration_endpoint = str(metadata.get("registration_endpoint") or "")
    if registration_endpoint == "":
        raise ValueError("授权服务器不支持动态注册，请改用「手动粘贴 Token」")
    body = {
        "client_name": "CrewLab",
        "redirect_uris": [redirect_uri()],
        "grant_types": ["authorization_code", "refresh_token"],
        "response_types": ["code"],
        "token_endpoint_auth_method": "none",
    }
    async with httpx.AsyncClient(timeout=REQUEST_TIMEOUT) as client:
        resp = await client.post(registration_endpoint, json=body)
        if resp.status_code not in (200, 201):
            raise ValueError(
                f"动态注册失败（HTTP {resp.status_code}），可改用「手动粘贴 Token」"
            )
        data = resp.json()
    if not isinstance(data, dict) or not data.get("client_id"):
        raise ValueError("动态注册响应缺少 client_id，可改用「手动粘贴 Token」")
    return data


def _pkce_pair() -> tuple[str, str]:
    """生成 PKCE verifier/challenge（S256）。"""

    verifier = secrets.token_urlsafe(48)
    digest = hashlib.sha256(verifier.encode("ascii")).digest()
    challenge = base64.urlsafe_b64encode(digest).rstrip(b"=").decode("ascii")
    return verifier, challenge


def _parse_dt(value: Any) -> datetime | None:
    if not isinstance(value, str) or value == "":
        return None
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed


def _wire_status(payload: dict[str, Any]) -> str:
    """wire 字段：none | authorized | expired。"""

    if str(payload.get("access_token") or "") == "":
        return "none"
    expires_at = _parse_dt(payload.get("expires_at"))
    if expires_at is not None and expires_at < datetime.now(timezone.utc):
        return "expired"
    return "authorized"


async def _get_token_row(
    db: AsyncSession, server_id: int
) -> McpOauthToken | None:
    return await db.scalar(
        select(McpOauthToken).where(McpOauthToken.server_id == server_id)
    )


def _merge_token(payload: dict[str, Any], token_payload: dict[str, Any], *, auth_source: str) -> None:
    """把 token 端点响应合并进 payload；refresh 响应缺 refresh_token 时保留旧值。"""

    payload["access_token"] = str(token_payload.get("access_token") or "")
    payload["refresh_token"] = str(
        token_payload.get("refresh_token") or payload.get("refresh_token") or ""
    )
    payload["token_type"] = str(token_payload.get("token_type") or "Bearer")
    payload["scope"] = str(token_payload.get("scope") or payload.get("scope") or "")
    payload["auth_source"] = auth_source
    expires_in = token_payload.get("expires_in")
    if isinstance(expires_in, (int, float)) and expires_in > 0:
        payload["expires_at"] = (
            datetime.now(timezone.utc) + timedelta(seconds=float(expires_in))
        ).isoformat()
    else:
        payload["expires_at"] = None


async def _request_token(
    metadata: dict[str, Any], client_info: dict[str, Any], data: dict[str, str]
) -> dict[str, Any]:
    """调 token 端点；client 认证方式按注册返回的 method 处理。"""

    token_endpoint = str(metadata.get("token_endpoint") or "")
    if token_endpoint == "":
        raise ValueError("元数据缺少 token_endpoint")
    auth_method = str(metadata.get("token_endpoint_auth_method") or "none")
    client_id = str(client_info.get("client_id") or "")
    client_secret = str(client_info.get("client_secret") or "")
    body = {**data, "client_id": client_id}
    auth = None
    if auth_method == "client_secret_basic" and client_secret:
        auth = httpx.BasicAuth(client_id, client_secret)
    elif auth_method == "client_secret_post" and client_secret:
        body["client_secret"] = client_secret
    async with httpx.AsyncClient(timeout=REQUEST_TIMEOUT) as client:
        resp = await client.post(token_endpoint, data=body, auth=auth)
        if resp.status_code != 200:
            raise ValueError(f"换取 token 失败（HTTP {resp.status_code}）")
        payload = resp.json()
    if not isinstance(payload, dict) or not payload.get("access_token"):
        raise ValueError("token 响应缺少 access_token")
    return payload


async def start_auth(db: AsyncSession, server: McpServer) -> dict[str, str]:
    """发起授权：发现 + 动态注册 + 生成 PKCE/state，返回授权页 URL。"""

    # 清掉过期 state，避免在途授权记录堆积
    await db.execute(
        delete(McpOauthState).where(
            McpOauthState.expires_at < datetime.now(timezone.utc)
        )
    )
    metadata = await discover_metadata(server.endpoint)
    client_info = await register_client(metadata)
    authorize_endpoint = str(metadata.get("authorization_endpoint") or "")
    if authorize_endpoint == "":
        raise ValueError("授权服务器元数据缺少 authorization_endpoint")
    verifier, challenge = _pkce_pair()
    state = secrets.token_urlsafe(32)
    db.add(
        McpOauthState(
            state=state,
            server_id=server.id,
            code_verifier=verifier,
            expires_at=datetime.now(timezone.utc)
            + timedelta(minutes=STATE_TTL_MINUTES),
        )
    )
    # client_info/metadata 预存 token 行，回调换 token 时取用；access 空 = 未授权
    row = await _get_token_row(db, server.id)
    if row is None:
        row = McpOauthToken(server_id=server.id)
        db.add(row)
    row.payload_ciphertext = encrypt_json(
        {
            "access_token": "",
            "refresh_token": "",
            "expires_at": None,
            "token_type": "",
            "scope": "",
            "client_info": client_info,
            "metadata": {
                "token_endpoint": str(metadata.get("token_endpoint") or ""),
                "token_endpoint_auth_method": str(
                    client_info.get("token_endpoint_auth_method") or "none"
                ),
            },
            "auth_source": "oauth",
        }
    )
    await db.commit()
    params = {
        "response_type": "code",
        "client_id": str(client_info["client_id"]),
        "redirect_uri": redirect_uri(),
        "state": state,
        "code_challenge": challenge,
        "code_challenge_method": "S256",
    }
    scopes = metadata.get("scopes_supported")
    if isinstance(scopes, list) and scopes:
        params["scope"] = " ".join(str(s) for s in scopes)
    sep = "&" if "?" in authorize_endpoint else "?"
    logger.info("发起 MCP OAuth 授权 server_id=%s", server.id)
    return {"authorize_url": f"{authorize_endpoint}{sep}{urlencode(params)}"}


async def handle_callback(db: AsyncSession, *, code: str, state: str) -> dict[str, Any]:
    """授权码回调：校验 state、换 token、落库并清理 state。"""

    now = datetime.now(timezone.utc)
    await db.execute(delete(McpOauthState).where(McpOauthState.expires_at < now))
    row = await db.scalar(select(McpOauthState).where(McpOauthState.state == state))
    if row is None or row.expires_at < now:
        raise ValueError("OAuth state 无效或已过期，请重新发起授权")
    token_row = await _get_token_row(db, row.server_id)
    if token_row is None or token_row.payload_ciphertext == "":
        raise ValueError("缺少注册信息，请重新发起授权")
    payload = decrypt_json(token_row.payload_ciphertext)
    token_payload = await _request_token(
        payload.get("metadata") or {},
        payload.get("client_info") or {},
        {
            "grant_type": "authorization_code",
            "code": code,
            "redirect_uri": redirect_uri(),
            "code_verifier": row.code_verifier,
        },
    )
    _merge_token(payload, token_payload, auth_source="oauth")
    token_row.payload_ciphertext = encrypt_json(payload)
    # state 一次性：回调即删，防重放
    await db.execute(delete(McpOauthState).where(McpOauthState.id == row.id))
    await db.commit()
    logger.info("MCP OAuth 授权完成 server_id=%s", row.server_id)
    return {"server_id": row.server_id}


async def save_manual_token(
    db: AsyncSession,
    server_id: int,
    *,
    access_token: str,
    refresh_token: str = "",
    expires_in: float | None = None,
) -> dict[str, Any]:
    """手动粘贴 Token 兜底；无 client 上下文，不参与自动刷新。"""

    if access_token.strip() == "":
        raise ValueError("access_token 不能为空")
    row = await _get_token_row(db, server_id)
    if row is None:
        row = McpOauthToken(server_id=server_id)
        db.add(row)
    payload: dict[str, Any] = {
        "access_token": access_token.strip(),
        "refresh_token": refresh_token.strip(),
        "expires_at": None,
        "token_type": "Bearer",
        "scope": "",
        "client_info": {},
        "metadata": {},
        "auth_source": "manual",
    }
    if isinstance(expires_in, (int, float)) and expires_in > 0:
        payload["expires_at"] = (
            datetime.now(timezone.utc) + timedelta(seconds=float(expires_in))
        ).isoformat()
    row.payload_ciphertext = encrypt_json(payload)
    await db.commit()
    logger.info("手动保存 MCP Token server_id=%s", server_id)
    return await oauth_status(db, server_id)


async def ensure_auth_headers(
    db: AsyncSession, server: McpServer
) -> dict[str, str] | None:
    """取 Authorization 头；临近过期自动刷新（仅 OAuth 来源且有 refresh_token）。"""

    row = await _get_token_row(db, server.id)
    if row is None or row.payload_ciphertext == "":
        return None
    payload = decrypt_json(row.payload_ciphertext)
    access = str(payload.get("access_token") or "")
    if access == "":
        return None
    expires_at = _parse_dt(payload.get("expires_at"))
    now = datetime.now(timezone.utc)
    if expires_at is not None and (expires_at - now).total_seconds() < REFRESH_LEEWAY_SECONDS:
        refresh = str(payload.get("refresh_token") or "")
        metadata = payload.get("metadata") or {}
        if payload.get("auth_source") == "oauth" and refresh and metadata.get("token_endpoint"):
            token_payload = await _request_token(
                metadata,
                payload.get("client_info") or {},
                {"grant_type": "refresh_token", "refresh_token": refresh},
            )
            _merge_token(payload, token_payload, auth_source="oauth")
            row.payload_ciphertext = encrypt_json(payload)
            await db.commit()
            access = str(payload.get("access_token") or "")
            logger.info("MCP OAuth token 已刷新 server_id=%s", server.id)
        else:
            raise ValueError("Token 已过期，请重新粘贴或重新发起 OAuth 授权")
    return {"Authorization": f"Bearer {access}"}


async def oauth_status(db: AsyncSession, server_id: int) -> dict[str, Any]:
    row = await _get_token_row(db, server_id)
    if row is None or row.payload_ciphertext == "":
        return {
            "authorized": False,
            "auth_source": None,
            "expires_at": None,
            "expired": False,
        }
    payload = decrypt_json(row.payload_ciphertext)
    expires_at = _parse_dt(payload.get("expires_at"))
    expired = (
        expires_at is not None and expires_at < datetime.now(timezone.utc)
    )
    access = str(payload.get("access_token") or "")
    return {
        "authorized": access != "" and not expired,
        "auth_source": payload.get("auth_source") or None,
        "expires_at": payload.get("expires_at"),
        "expired": expired and access != "",
    }


async def status_map(db: AsyncSession, server_ids: list[int]) -> dict[int, str]:
    """批量取 wire 状态串（列表页用），坏密文按 none 处理不影响列表。"""

    if not server_ids:
        return {}
    rows = (
        await db.scalars(
            select(McpOauthToken).where(McpOauthToken.server_id.in_(server_ids))
        )
    ).all()
    out: dict[int, str] = {}
    for row in rows:
        try:
            out[row.server_id] = _wire_status(decrypt_json(row.payload_ciphertext))
        except Exception:  # noqa: BLE001 - 坏密文只影响该行展示
            out[row.server_id] = "none"
    return out


async def revoke(db: AsyncSession, server_id: int) -> None:
    """解绑：删 token 与在途 state。"""

    await db.execute(
        delete(McpOauthToken).where(McpOauthToken.server_id == server_id)
    )
    await db.execute(
        delete(McpOauthState).where(McpOauthState.server_id == server_id)
    )
    await db.commit()
    logger.info("解绑 MCP OAuth server_id=%s", server_id)
