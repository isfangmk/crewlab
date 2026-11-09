"""设置页 LLM 探测 / 拉取模型 / 保存。"""

from __future__ import annotations

import logging

import httpx
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import require_auth
from app.db import get_db
from app.services import llm_admin

logger = logging.getLogger(__name__)

router = APIRouter(tags=["integrations-llm"])


class LLMConfigIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    provider: str = Field(min_length=1)
    api_key: str
    base_url: str
    default_model: str
    models: list[str]
    # 跳过 TLS 证书校验（自签证书场景）；必须真正作用到 HTTP 客户端
    tls_insecure: bool = False
    # litellm 模型名前缀（如 openai/）；空串表示不加前缀
    model_prefix: str = ""
    # 厂商专属请求体（JSON 对象文本），如 DeepSeek 的 {"thinking":{"type":"enabled"}}
    extra_body_json: str = ""


@router.post("/integrations/llm/test")
async def test_llm(
    body: LLMConfigIn,
    _user: str = Depends(require_auth),
) -> dict:
    try:
        return await llm_admin.probe_configuration(
            provider=body.provider,
            api_key=body.api_key,
            base_url=body.base_url,
            default_model=body.default_model,
            models=body.models,
            tls_insecure=body.tls_insecure,
        )
    except (ValueError, TypeError) as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except httpx.HTTPError as exc:
        # 上游厂商不可达（连接失败/超时/TLS 错误）：显式失败并回传原因，
        # 不能让 httpx 异常冒泡成 500 的 ASGI traceback。
        logger.warning("LLM 探测失败 provider=%s: %s", body.provider, exc)
        raise HTTPException(status_code=502, detail=f"厂商连接失败: {exc}") from exc


@router.post("/integrations/llm/validate-and-save")
async def validate_and_save(
    body: LLMConfigIn,
    db: AsyncSession = Depends(get_db),
    _user: str = Depends(require_auth),
) -> dict:
    try:
        return await llm_admin.validate_and_save(
            db,
            provider=body.provider,
            api_key=body.api_key,
            base_url=body.base_url,
            default_model=body.default_model,
            models=body.models,
            tls_insecure=body.tls_insecure,
            model_prefix=body.model_prefix,
            extra_body_json=body.extra_body_json,
        )
    except (ValueError, TypeError) as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except httpx.HTTPError as exc:
        # 上游厂商不可达：显式失败，不能让 httpx 异常冒泡成 500
        logger.warning("保存 LLM 配置失败（厂商不可达）provider=%s: %s", body.provider, exc)
        raise HTTPException(status_code=502, detail=f"厂商连接失败: {exc}") from exc


@router.post("/integrations/llm/models")
async def list_remote_models(
    body: LLMConfigIn,
    _user: str = Depends(require_auth),
) -> dict:
    try:
        models = await llm_admin.fetch_remote_models(
            base_url=body.base_url,
            api_key=body.api_key,
            tls_insecure=body.tls_insecure,
        )
    except (ValueError, TypeError, RuntimeError) as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except httpx.HTTPError as exc:
        # 上游厂商不可达：显式失败，不能让 httpx 异常冒泡成 500
        logger.warning("拉取厂商模型列表失败 base_url=%s: %s", body.base_url, exc)
        raise HTTPException(status_code=502, detail=f"厂商连接失败: {exc}") from exc
    return {"models": models}
