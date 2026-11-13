"""内置 Tool 列表与手动运行 API（侧边栏 /tools）。"""

from __future__ import annotations

import json

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import require_auth, require_not_viewer
from app.db import get_db
from app.schemas.tools import (
    BuiltinToolGroupOut,
    BuiltinToolListOut,
    BuiltinToolOut,
    BuiltinToolRunIn,
    BuiltinToolRunOut,
)
from app.services import builtin_tool_service
from app.services.audit_service import write_audit
from app.services.auth_service import AuthUser

router = APIRouter(tags=["tools"])

_DETAIL_LIMIT = 500


def _detail_summary(value: object) -> str:
    """审计只存参数/结果摘要，避免把整份工具输出写进日志表。"""

    text = json.dumps(value, ensure_ascii=False, default=str)
    return text if len(text) <= _DETAIL_LIMIT else text[:_DETAIL_LIMIT] + "…"


@router.get("/tools/builtin", response_model=BuiltinToolListOut)
async def list_builtin_tools(_user: str = Depends(require_auth)) -> BuiltinToolListOut:
    items = [BuiltinToolOut(**row) for row in builtin_tool_service.list_builtin_tools()]
    groups = [
        BuiltinToolGroupOut(**row)
        for row in builtin_tool_service.list_builtin_tool_groups()
    ]
    return BuiltinToolListOut(items=items, groups=groups, total=len(items))


@router.post("/tools/builtin/{name}/run", response_model=BuiltinToolRunOut)
async def run_builtin_tool(
    name: str,
    body: BuiltinToolRunIn,
    db: AsyncSession = Depends(get_db),
    user: AuthUser = Depends(require_not_viewer),
) -> BuiltinToolRunOut:
    """手动探测内置 Tool；与对话调用共用同一实现。"""

    try:
        payload = await builtin_tool_service.run_builtin_tool(db, name, body.params)
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except (ValueError, TypeError) as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except RuntimeError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    await write_audit(
        actor_user_id=user.id,
        action="tool.run",
        resource_type="builtin_tool",
        resource_id=name,
        detail={"params": _detail_summary(body.params), "result": _detail_summary(payload)},
    )
    return BuiltinToolRunOut(**payload)
