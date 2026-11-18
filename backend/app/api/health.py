"""健康检查。"""

from fastapi import APIRouter

router = APIRouter(tags=["health"])


@router.get("/healthz")
async def healthz() -> dict[str, str]:
    """健康检查探针；服务存活即返回 ok。"""

    return {"status": "ok"}
