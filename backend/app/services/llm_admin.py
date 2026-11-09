"""设置页 LLM 配置：与 llm_providers 同步，并做连通性探测。"""

from __future__ import annotations

import json
import logging
import time
from datetime import datetime, timezone

import httpx
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models_orm.llm import AppSetting, LlmProvider
from app.services.llm_config import split_models

logger = logging.getLogger(__name__)

_MASK = "********"


def _provider_keys(provider_id: str) -> dict[str, str]:
    return {
        "api_key": f"{provider_id}_api_key",
        "base_url": f"{provider_id}_base_url",
        "models": f"{provider_id}_models",
        "default_model": f"{provider_id}_default_model",
        "model_prefix": f"{provider_id}_model_prefix",
        "extra_body_json": f"{provider_id}_extra_body_json",
    }


def _iso_now() -> str:
    return datetime.now(timezone.utc).isoformat()


async def list_all_providers(db: AsyncSession) -> list[LlmProvider]:
    result = await db.scalars(select(LlmProvider).order_by(LlmProvider.id))
    return list(result.all())


async def list_llm_settings(db: AsyncSession) -> list[dict]:
    """把 llm_providers 展平为前端 system-settings(category=llm) 形状。"""

    providers = await list_all_providers(db)
    now = _iso_now()
    items: list[dict] = []
    for p in providers:
        keys = _provider_keys(p.provider_id)
        models = split_models(p.models) if p.models else []
        # 未启用或空密钥：前端据此隐藏/视为未配置
        api_display = _MASK if p.enabled and p.api_key != "" else ""
        items.extend(
            [
                {
                    "category": "llm",
                    "key": keys["api_key"],
                    "value": api_display,
                    "sensitive": True,
                    "updated_at": now,
                },
                {
                    "category": "llm",
                    "key": keys["base_url"],
                    "value": p.base_url,
                    "sensitive": False,
                    "updated_at": now,
                },
                {
                    "category": "llm",
                    "key": keys["models"],
                    "value": json.dumps(models, ensure_ascii=False),
                    "sensitive": False,
                    "updated_at": now,
                },
                {
                    "category": "llm",
                    "key": keys["default_model"],
                    "value": p.default_model,
                    "sensitive": False,
                    "updated_at": now,
                },
                {
                    "category": "llm",
                    "key": keys["model_prefix"],
                    "value": p.model_prefix,
                    "sensitive": False,
                    "updated_at": now,
                },
                {
                    "category": "llm",
                    "key": keys["extra_body_json"],
                    "value": p.extra_body_json,
                    "sensitive": False,
                    "updated_at": now,
                },
            ]
        )
    return items


async def reveal_api_key(db: AsyncSession, provider_id: str) -> str:
    row = await db.scalar(select(LlmProvider).where(LlmProvider.provider_id == provider_id))
    if row is None:
        raise LookupError(f"未找到 provider: {provider_id}")
    if row.api_key == "":
        raise LookupError(f"provider 无 API key: {provider_id}")
    return row.api_key


async def probe_chat(
    *,
    base_url: str,
    api_key: str,
    model: str,
    tls_insecure: bool = False,
) -> dict:
    """对单个模型发最小 chat.completions 请求。"""

    if base_url == "":
        raise ValueError("base_url 不能为空")
    if api_key == "":
        raise ValueError("api_key 不能为空")
    if model == "":
        raise ValueError("model 不能为空")

    url = f"{base_url.rstrip('/')}/chat/completions"
    payload = {
        "model": model,
        "messages": [{"role": "user", "content": "ping"}],
        "max_tokens": 1,
    }
    headers = {"Authorization": f"Bearer {api_key}"}
    started = time.perf_counter()
    if tls_insecure:
        logger.warning("探测 %s 跳过 TLS 证书校验（自签证书场景）", model)
    async with httpx.AsyncClient(timeout=30.0, verify=not tls_insecure) as client:
        resp = await client.post(url, json=payload, headers=headers)
    latency_ms = int((time.perf_counter() - started) * 1000)
    if resp.status_code != 200:
        return {
            "valid": False,
            "code": f"http_{resp.status_code}",
            "provider": "",
            "model": model,
            "detail": resp.text[:500],
            "latency_ms": latency_ms,
            "saved": False,
            "disabled": False,
        }
    return {
        "valid": True,
        "code": "ok",
        "provider": "",
        "model": model,
        "latency_ms": latency_ms,
        "saved": False,
        "disabled": False,
    }


async def probe_configuration(
    *,
    provider: str,
    api_key: str,
    base_url: str,
    default_model: str,
    models: list[str],
    tls_insecure: bool = False,
) -> dict:
    """探测 draft：优先 default_model，再扫 models 列表。"""

    if provider == "":
        raise ValueError("provider 不能为空")
    targets = list(models)
    if default_model != "" and default_model not in targets:
        targets.insert(0, default_model)
    if not targets:
        raise ValueError("models 不能为空")

    last: dict | None = None
    for m in targets:
        last = await probe_chat(
            base_url=base_url,
            api_key=api_key,
            model=m,
            tls_insecure=tls_insecure,
        )
        last["provider"] = provider
        if not last["valid"]:
            return last
    if last is None:
        raise RuntimeError("探测未执行")
    return last


async def fetch_remote_models(
    *, base_url: str, api_key: str, tls_insecure: bool = False
) -> list[str]:
    if base_url == "":
        raise ValueError("base_url 不能为空")
    if api_key == "":
        raise ValueError("api_key 不能为空")
    url = f"{base_url.rstrip('/')}/models"
    headers = {"Authorization": f"Bearer {api_key}"}
    if tls_insecure:
        logger.warning("拉取 %s 模型列表跳过 TLS 证书校验", base_url)
    async with httpx.AsyncClient(timeout=30.0, verify=not tls_insecure) as client:
        resp = await client.get(url, headers=headers)
    if resp.status_code != 200:
        raise RuntimeError(f"拉取模型列表失败 HTTP {resp.status_code}: {resp.text[:300]}")
    body = resp.json()
    if "data" not in body or not isinstance(body["data"], list):
        raise RuntimeError("模型列表响应缺少 data 数组")
    names: list[str] = []
    for row in body["data"]:
        if not isinstance(row, dict) or "id" not in row:
            raise RuntimeError("模型列表项缺少 id")
        mid = row["id"]
        if not isinstance(mid, str) or mid == "":
            raise RuntimeError("模型 id 非法")
        names.append(mid)
    return names


async def validate_and_save(
    db: AsyncSession,
    *,
    provider: str,
    api_key: str,
    base_url: str,
    default_model: str,
    models: list[str],
    tls_insecure: bool = False,
    model_prefix: str = "",
    extra_body_json: str = "",
) -> dict:
    """空 api_key = 禁用该 provider；非空则探测通过后写入。"""

    if provider == "":
        raise ValueError("provider 不能为空")
    if extra_body_json != "":
        # 非法 JSON 必须显式失败，不能存进去等运行时炸
        try:
            parsed = json.loads(extra_body_json)
        except json.JSONDecodeError as exc:
            raise ValueError("extra_body_json 不是合法 JSON") from exc
        if not isinstance(parsed, dict):
            raise ValueError("extra_body_json 必须是 JSON 对象")
    row = await db.scalar(select(LlmProvider).where(LlmProvider.provider_id == provider))

    if api_key == "":
        if row is None:
            # 没有可禁用的 provider：显式失败，不谎报 saved
            raise ValueError(
                f"provider 不存在: {provider}（空 api_key 表示禁用，必须先存在该 provider）"
            )
        # 空 api_key = 禁用该 provider，但保留已存密钥：
        # 删除密钥不可恢复；停用只需清空 key，不必删行。
        row.enabled = False
        # default_provider 不能指向刚被禁用的 provider：
        # 否则模型目录会整体报错，且用户拿不到任何可选模型。
        setting = await db.get(AppSetting, "default_provider")
        if setting is not None and setting.value == provider:
            await db.delete(setting)
            logger.info(
                "禁用 provider=%s 同时清除其 default_provider 指向", provider
            )
        await db.commit()
        logger.info("已禁用 LLM provider=%s（保留已存密钥）", provider)
        return {
            "valid": True,
            "code": "disabled",
            "provider": provider,
            "model": default_model,
            "latency_ms": 0,
            "saved": True,
            "disabled": True,
        }

    if base_url == "":
        raise ValueError("base_url 不能为空")
    if not isinstance(models, list) or len(models) == 0:
        raise ValueError("models 不能为空")
    for i, m in enumerate(models):
        if not isinstance(m, str) or m == "":
            raise ValueError(f"models[{i}] 非法")
    if default_model == "":
        raise ValueError("default_model 不能为空")
    if default_model not in models:
        raise ValueError("default_model 必须在 models 中")

    probe = await probe_configuration(
        provider=provider,
        api_key=api_key,
        base_url=base_url,
        default_model=default_model,
        models=models,
        tls_insecure=tls_insecure,
    )
    if not probe["valid"]:
        return probe

    models_csv = ",".join(models)
    if row is None:
        row = LlmProvider(
            provider_id=provider,
            label=provider,
            base_url=base_url,
            api_key=api_key,
            models=models_csv,
            default_model=default_model,
            enabled=True,
            model_prefix=model_prefix,
            extra_body_json=extra_body_json,
        )
        db.add(row)
    else:
        row.label = provider
        row.base_url = base_url
        row.api_key = api_key
        row.models = models_csv
        row.default_model = default_model
        row.enabled = True
        row.model_prefix = model_prefix
        row.extra_body_json = extra_body_json

    setting = await db.get(AppSetting, "default_provider")
    if setting is None:
        db.add(AppSetting(key="default_provider", value=provider))
    await db.commit()
    logger.info(
        "已保存 LLM provider=%s models=%s default=%s",
        provider,
        models_csv,
        default_model,
    )
    probe["saved"] = True
    probe["disabled"] = False
    probe["provider"] = provider
    return probe
