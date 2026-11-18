"""平台助手元工具：语义检索平台接口 + 以当前用户身份调用平台 API。

只暴露两个工具代替大量 CRUD 接口（避免工具爆炸）：
  - search_platform_api：按自然语言检索相关接口，返回 api_id / 参数说明。
  - call_platform_api：按目录项校验参数后，经进程内 ASGITransport 调用平台自身 API。

身份来自会话归属（ChatSession.owner_user_id），不是外部输入，也不是请求 ContextVar
（后台回合不继承）。权限不在这里实现：内部凭证带 actor 调目标接口，require_admin /
require_not_viewer 原样生效，普通用户调 admin 接口照样 403——与页面操作一致。
"""

from __future__ import annotations

import json
import logging
import re
from typing import Any

import httpx

from app.main import app as fastapi_app
from app.services.observability.audit_service import write_audit
from app.services.platform.api_catalog import PlatformApiEntry, entry_by_id
from app.services.platform.internal_call import internal_call_headers
from app.services.platform.platform_api_index import search_platform_apis as _semantic_search

logger = logging.getLogger(__name__)

_PATH_PARAM_RE = re.compile(r"\{(\w+)\}")


def _error_result(tool_name: str, exc: Exception) -> dict:
    reason = str(exc) or type(exc).__name__
    logger.warning("平台工具执行失败 tool=%s err=%s", tool_name, exc)
    return {
        "error": (
            f"工具 {tool_name} 执行失败：{reason}。"
            "请按报错原因修正参数后重试，或改用其他方式继续。"
        )
    }


async def _resolve_actor(tool_context: Any) -> int:
    """从工具上下文反查当前用户 id（会话归属），不用 contextvar。

    与 agent_tool_service / memory.py 同款模式：执行期从 ADK session.id 查 ChatSession。
    查不到显式失败，绝不退化成 admin 或其他用户。
    """
    session = getattr(tool_context, "session", None) if tool_context is not None else None
    session_id = getattr(session, "id", "") or ""
    if not session_id:
        raise ValueError("缺少会话上下文，无法确定操作身份")
    from app.db import SessionLocal
    from app.models.chat import ChatSession

    async with SessionLocal() as db:
        row = await db.get(ChatSession, session_id)
        if row is None or row.owner_user_id is None:
            raise ValueError("会话未绑定用户，无法确定操作身份")
        return int(row.owner_user_id)


def build_internal_request(
    entry: PlatformApiEntry, arguments: Any
) -> tuple[str, dict, dict | None]:
    """按目录项把 arguments 拆成 path / query / body，并校验必填项。

    - path 占位符（{id}）必须由 arguments 提供，替换进 URL；
    - OpenAPI 标 required 的 query 参数必须提供；
    - requestBody 为 application/json 的 schema：若声明 required 字段则必须提供；
    - 未声明的多余参数显式拒绝，避免模型拼错参数还蒙混调用。
    """
    if not isinstance(arguments, dict):
        raise ValueError("arguments 必须是对象")

    provided = dict(arguments)
    path = entry.path
    for name in _PATH_PARAM_RE.findall(entry.path):
        if name not in provided:
            raise ValueError(f"缺少路径参数: {name}")
        value = provided.pop(name)
        if isinstance(value, (dict, list)):
            raise ValueError(f"路径参数 {name} 不能是对象")
        path = path.replace(f"{{{name}}}", str(value))

    # query 参数：目录中声明的 + 模型给的（剔除内部头）
    query_schema = {p["name"]: p for p in entry.parameters if p.get("in") == "query"}
    for name, spec in query_schema.items():
        if spec.get("required") and name not in provided:
            raise ValueError(f"缺少必填查询参数: {name}")
    query = {}
    for name in list(provided):
        if name in query_schema:
            query[name] = provided.pop(name)

    # body：body schema 的 required 字段必须在 arguments（或嵌套 body）里
    body: dict | None = None
    if entry.body_schema is not None:
        required = entry.body_schema.get("required", [])
        # 兼容两种传法：arguments 直接给 body 字段，或包一层 {"body": {...}}
        nested = provided.pop("body", None)
        if isinstance(nested, dict):
            body = nested
        elif nested is not None:
            raise ValueError("body 必须是对象")
        else:
            body = {k: v for k, v in provided.items()}
            provided = {}
        for field in required:
            if field not in body:
                raise ValueError(f"缺少必填请求体字段: {field}")
    if provided:
        raise ValueError(f"接口 {entry.api_id} 不接受参数: {sorted(provided)}")
    return path, query, body


async def _call_api(
    *,
    api_id: str,
    arguments: dict,
    actor_user_id: int,
) -> dict:
    entry = entry_by_id(api_id)
    if entry is None:
        raise LookupError(f"接口不存在: {api_id}；请先用 search_platform_api 检索")
    path, query, body = build_internal_request(entry, arguments)

    transport = httpx.ASGITransport(app=fastapi_app)
    async with httpx.AsyncClient(
        transport=transport, base_url="http://platform-internal", timeout=60.0
    ) as client:
        response = await client.request(
            entry.method,
            path,
            params=query or None,
            json=body,
            headers=internal_call_headers(actor_user_id),
        )

    # 摘要：只回状态与结果片段，避免大响应撑爆上下文（after_tool_callback 还会再截）
    text = response.text
    result: Any
    try:
        result = response.json()
    except ValueError:
        result = text[:2000]
    summary = result if isinstance(result, (dict, list)) else {"value": result}
    if isinstance(summary, dict):
        summary = {k: summary[k] for k in list(summary)[:30]}

    await write_audit(
        actor_user_id=actor_user_id,
        action="platform_api.call",
        resource_type="platform_api",
        resource_id=entry.api_id,
        detail={
            "method": entry.method,
            "path": path,
            "status_code": response.status_code,
            "params": {k: str(v)[:100] for k, v in (arguments or {}).items()},
        },
    )
    logger.info(
        "平台助手调用接口 actor=%s api=%s status=%s",
        actor_user_id,
        entry.api_id,
        response.status_code,
    )
    return {
        "status_code": response.status_code,
        "ok": 200 <= response.status_code < 300,
        "api_id": entry.api_id,
        "result": summary,
    }


async def search_platform_api(
    query: str, tool_context: Any = None  # pyright: ignore[reportUnusedParameter]
) -> dict:
    """按自然语言检索平台可操作的接口，返回候选 api_id 与参数说明。

    Args:
        query: 要做什么，如「查询工作流运行记录」「删除一条记忆」。
    """
    try:
        hits = await _semantic_search(query, limit=5)
        return {"query": query, "candidates": hits}
    except Exception as exc:  # noqa: BLE001 — 异常转结构化结果
        return _error_result("search_platform_api", exc)


async def call_platform_api(
    api_id: str,
    arguments: str = "{}",
    tool_context: Any = None,
) -> dict:
    """按 api_id 调用一个平台接口（写操作需人工审批）。

    先用 search_platform_api 找到 api_id。arguments 是 JSON 对象字符串，
    包含路径参数、查询参数或请求体字段（如 {"memory_id": "123"}）。
    查询不需要审批；新增/修改/删除会弹审批卡片让用户确认。

    Args:
        api_id: search_platform_api 返回的接口标识，如 "GET /api/v1/memory"。
        arguments: JSON 对象字符串，接口的路径/查询/请求体参数。
    """
    try:
        try:
            parsed = json.loads(arguments) if isinstance(arguments, str) else arguments
        except json.JSONDecodeError as exc:
            raise ValueError(f"arguments 不是合法 JSON: {exc}") from exc
        if not isinstance(parsed, dict):
            raise ValueError("arguments 必须是 JSON 对象")
        actor = await _resolve_actor(tool_context)
        return await _call_api(api_id=api_id, arguments=parsed, actor_user_id=actor)
    except Exception as exc:  # noqa: BLE001 — 异常转结构化结果
        return _error_result("call_platform_api", exc)
