"""版本信息。"""

from __future__ import annotations

from importlib.metadata import PackageNotFoundError, version

from fastapi import APIRouter, Depends, HTTPException

from app.api.deps import require_auth

router = APIRouter(tags=["version"])


@router.get("/version")
async def get_version(_user: str = Depends(require_auth)) -> dict:
    try:
        ver = version("ai-workbench")
    except PackageNotFoundError as exc:
        # 打包信息缺失是部署问题，不能伪装成 0.1.0 让前端以为是正常版本
        raise HTTPException(
            status_code=503,
            detail="无法读取后端包版本信息（ai-workbench 未安装或元数据损坏）",
        ) from exc
    return {"manager_version": ver}
