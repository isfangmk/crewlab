"""内置 Tool 列表与手动运行 API（侧边栏 /tools）。"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import require_auth
from app.db import get_db
from app.schemas.tools import (
    BuiltinToolListOut,
    BuiltinToolOut,
    BuiltinToolRunIn,
    BuiltinToolRunOut,
)
from app.services import builtin_tool_service

router = APIRouter(tags=["tools"])


@router.get("/tools/builtin", response_model=BuiltinToolListOut)
async def list_builtin_tools(_user: str = Depends(require_auth)) -> BuiltinToolListOut:
    items = [BuiltinToolOut(**row) for row in builtin_tool_service.list_builtin_tools()]
    return BuiltinToolListOut(items=items, total=len(items))


@router.post("/tools/builtin/{name}/run", response_model=BuiltinToolRunOut)
async def run_builtin_tool(
    name: str,
    body: BuiltinToolRunIn,
    db: AsyncSession = Depends(get_db),
    _user: str = Depends(require_auth),
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
    return BuiltinToolRunOut(**payload)
