"""对话可用模型目录：只读配置-模型。"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import require_auth
from app.db import get_db
from app.schemas.chat import LLMProviderOut, ModelCatalogOut
from app.services import llm_config

router = APIRouter(tags=["models"])


@router.get("/aiops/models", response_model=ModelCatalogOut)
async def list_models(
    db: AsyncSession = Depends(get_db),
    _user: str = Depends(require_auth),
) -> ModelCatalogOut:
    providers = await llm_config.list_enabled_providers(db)
    try:
        default_provider, default_model = await llm_config.get_default_selection(db)
    except LookupError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc

    items: list[LLMProviderOut] = []
    for p in providers:
        # 空 models 是数据损坏（provider 配置必须有模型），显式报 409，
        # 不能让 split_models 的 ValueError 变成整目录 500。
        if not p.models.strip():
            raise HTTPException(
                status_code=409,
                detail=f"provider {p.provider_id} 的模型列表为空，请在设置里重新保存该 provider",
            )
        models = llm_config.split_models(p.models)
        items.append(
            LLMProviderOut(
                id=p.provider_id,
                label=p.label,
                models=models,
                model=p.default_model,
            )
        )
    return ModelCatalogOut(
        providers=items,
        default={"provider": default_provider, "model": default_model},
    )
