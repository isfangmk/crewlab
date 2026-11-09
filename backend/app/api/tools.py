"""内置 Tool 列表 API（侧边栏 /tools）。"""

from __future__ import annotations

from fastapi import APIRouter, Depends

from app.adk_runtime.builtin_tools import BUILTIN_TOOL_SPECS
from app.api.deps import require_auth
from app.schemas.tools import BuiltinToolListOut, BuiltinToolOut

router = APIRouter(tags=["tools"])


@router.get("/tools/builtin", response_model=BuiltinToolListOut)
async def list_builtin_tools(_user: str = Depends(require_auth)) -> BuiltinToolListOut:
    items = [BuiltinToolOut(**spec) for spec in BUILTIN_TOOL_SPECS]
    return BuiltinToolListOut(items=items, total=len(items))
