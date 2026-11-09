"""配置-模型 读写；对话模型列表只来这里。"""

from __future__ import annotations

import json
import logging
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.models_orm.llm import AppSetting, LlmProvider

logger = logging.getLogger(__name__)


def split_models(raw: str) -> list[str]:
    """解析逗号分隔模型列表；空串直接失败。"""

    parts = [p.strip() for p in raw.split(",")]
    models = [p for p in parts if p]
    if not models:
        raise ValueError("models 不能为空")
    return models


def compose_litellm_model(
    *,
    provider_id: str,
    model_prefix: str,
    model: str,
) -> str:
    """拼出传给 LiteLLM 的 model 字符串。

    页面与库只存上游模型 id；装配 agent 时再拼厂商前缀。
    custom 必须配置非空前缀（OpenAI 兼容端点用 openai/）。
    """

    if model == "":
        raise ValueError("model 不能为空")
    if provider_id == "custom" and model_prefix == "":
        raise ValueError("custom 的 model_prefix 不能为空")
    if model_prefix == "":
        return model
    return f"{model_prefix}{model}"


def provider_extra_body(provider: LlmProvider) -> dict[str, Any]:
    """解析 provider 的厂商专属请求体。

    空串表示没有额外请求体；配了非法 JSON 必须显式失败，
    不能静默忽略（那会让用户以为配置生效了）。
    """

    raw = provider.extra_body_json
    if raw == "":
        return {}
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise ValueError(
            f"provider {provider.provider_id} 的 extra_body_json 不是合法 JSON"
        ) from exc
    if not isinstance(parsed, dict):
        raise TypeError(
            f"provider {provider.provider_id} 的 extra_body_json 必须是 JSON 对象"
        )
    return parsed


async def ensure_seed_provider(db: AsyncSession) -> None:
    """若库中无任何 Provider，用环境变量种子写入一条。

    种子 provider 已存在时只补齐「新增字段」：
    前缀 / 厂商请求体是后加的列，老库里是空值，
    不补的话对话会拿不到 openai/ 前缀而请求失败。
    """

    settings = get_settings()
    existing = await db.scalar(select(LlmProvider.id).limit(1))
    if existing is not None:
        seed = await db.scalar(
            select(LlmProvider).where(
                LlmProvider.provider_id == settings.seed_llm_provider
            )
        )
        if seed is not None:
            changed = False
            if seed.model_prefix == "" and settings.seed_llm_model_prefix != "":
                seed.model_prefix = settings.seed_llm_model_prefix
                changed = True
            if (
                seed.extra_body_json == ""
                and settings.seed_llm_extra_body_json != ""
            ):
                seed.extra_body_json = settings.seed_llm_extra_body_json
                changed = True
            if changed:
                await db.commit()
                logger.info(
                    "已补齐种子 provider 的前缀/厂商请求体 provider=%s",
                    settings.seed_llm_provider,
                )
        return
    provider = LlmProvider(
        provider_id=settings.seed_llm_provider,
        label=settings.seed_llm_provider,
        base_url=settings.seed_llm_base_url,
        api_key=settings.seed_llm_api_key,
        models=settings.seed_llm_model,
        default_model=settings.seed_llm_model,
        enabled=True,
        model_prefix=settings.seed_llm_model_prefix,
        extra_body_json=settings.seed_llm_extra_body_json,
    )
    db.add(provider)
    db.add(AppSetting(key="default_provider", value=settings.seed_llm_provider))
    await db.commit()
    logger.info(
        "已写入种子模型配置 provider=%s model=%s",
        settings.seed_llm_provider,
        settings.seed_llm_model,
    )


async def list_enabled_providers(db: AsyncSession) -> list[LlmProvider]:
    result = await db.scalars(
        select(LlmProvider).where(LlmProvider.enabled.is_(True)).order_by(LlmProvider.id)
    )
    providers = list(result.all())
    return providers


async def get_provider(db: AsyncSession, provider_id: str) -> LlmProvider:
    provider = await db.scalar(
        select(LlmProvider).where(
            LlmProvider.provider_id == provider_id,
            LlmProvider.enabled.is_(True),
        )
    )
    if provider is None:
        raise LookupError(f"未找到启用的 provider: {provider_id}")
    return provider


async def resolve_model(
    db: AsyncSession, provider_id: str, model: str
) -> LlmProvider:
    """校验 provider+model 必须存在于配置-模型中。"""

    provider = await get_provider(db, provider_id)
    models = split_models(provider.models)
    if model not in models:
        raise LookupError(f"模型不在配置中: provider={provider_id} model={model}")
    return provider


async def get_default_selection(db: AsyncSession) -> tuple[str, str]:
    providers = await list_enabled_providers(db)
    if not providers:
        raise LookupError("配置-模型为空，无法提供默认模型")
    setting = await db.get(AppSetting, "default_provider")
    if setting is None:
        raise LookupError("缺少 default_provider 设置")
    provider = await get_provider(db, setting.value)
    return provider.provider_id, provider.default_model
